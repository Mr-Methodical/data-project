"""Transactional, append-only evidence and review storage.

This is a local single-operator application. A reviewer is a self-reported
label, not an authenticated identity. Hash chaining detects accidental or
partial modification; it cannot prevent an administrator rewriting a database.
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .engine import RULE_VERSION, analyze, canonical_csv, candidate_evidence_hash, standings

MAX_SOURCE_BYTES = 1024 * 1024


class StoreError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def candidate_hash(candidate: dict) -> str:
    """Hash evidence, not positional candidate IDs or positional provenance."""
    return candidate_evidence_hash(candidate)


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    scorer_csv TEXT NOT NULL,
    league_csv TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    analysis_hash TEXT NOT NULL,
    is_demo INTEGER NOT NULL CHECK(is_demo IN (0, 1))
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(id),
    case_id TEXT,
    action TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_run ON audit(run_id, seq);
CREATE TABLE IF NOT EXISTS resolutions (
    run_id TEXT NOT NULL REFERENCES runs(id),
    case_id TEXT NOT NULL,
    case_fingerprint TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('select', 'exclude')),
    candidate_id TEXT,
    candidate_hash TEXT,
    note TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES audit(id),
    origin_event_id TEXT REFERENCES audit(id),
    origin_run_id TEXT REFERENCES runs(id),
    replayed_from_json TEXT,
    PRIMARY KEY(run_id, case_id)
);
CREATE INDEX IF NOT EXISTS resolution_replay ON resolutions(case_fingerprint, rule_version, origin_event_id);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_update BEFORE UPDATE ON runs
BEGIN SELECT RAISE(ABORT, 'Raw evidence and analyses are immutable'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_delete BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'Raw evidence and analyses are immutable'); END;
CREATE TRIGGER IF NOT EXISTS resolutions_no_update BEFORE UPDATE ON resolutions
BEGIN SELECT RAISE(ABORT, 'Accepted decisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS resolutions_no_delete BEFORE DELETE ON resolutions
BEGIN SELECT RAISE(ABORT, 'Accepted decisions are immutable'); END;
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if self.db_path == ":memory:":
            raise ValueError("Use a disk-backed SQLite path; each request opens its own connection.")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
        finally:
            db.close()

    @contextmanager
    def connection(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            else:
                # Readers see one consistent snapshot of evidence + decisions.
                db.execute("BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def empty(self) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM runs LIMIT 1").fetchone() is None

    @staticmethod
    def _run(db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise StoreError(404, "Run not found.")
        return row

    @staticmethod
    def _events(db: sqlite3.Connection, run_id: str) -> list[dict]:
        rows = db.execute("SELECT * FROM audit WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
        return [{"id": row["id"], "run_id": row["run_id"], "case_id": row["case_id"],
                 "action": row["action"], "reviewer": row["reviewer"], "note": row["note"],
                 "created_at": row["created_at"], "details": json.loads(row["details_json"]),
                 "previous_hash": row["previous_hash"], "event_hash": row["event_hash"]} for row in rows]

    def _assert_integrity(self, db: sqlite3.Connection, row: sqlite3.Row) -> None:
        try:
            if digest(row["analysis_json"]) != row["analysis_hash"]:
                raise ValueError("Analysis hash mismatch")
            analysis = json.loads(row["analysis_json"])
            if analysis["rule_version"] != row["rule_version"]:
                raise ValueError("Rule version mismatch")
            for source in analysis["sources"]:
                if digest(row[f"{source['id']}_csv"]) != source["sha256"]:
                    raise ValueError("Source hash mismatch")
            fingerprint = self._fingerprint(row["scorer_csv"], row["league_csv"], row["rule_version"])
            if fingerprint != row["fingerprint"]:
                raise ValueError("Pair fingerprint mismatch")
            previous = ""
            events = self._events(db, row["id"])
            if not events or events[0]["action"] != "ingest":
                raise ValueError("Missing ingestion event")
            if events[0]["details"].get("fingerprint") != fingerprint or events[0]["details"].get("sources") != analysis["sources"]:
                raise ValueError("Ingestion audit mismatch")
            for event in events:
                body = {key: value for key, value in event.items() if key != "event_hash"}
                if event["previous_hash"] != previous or digest(canonical_json(body)) != event["event_hash"]:
                    raise ValueError("Audit chain mismatch")
                previous = event["event_hash"]
            event_map = {event["id"]: event for event in events}
            cases = {case["id"]: case for case in analysis["cases"]}
            candidates = {candidate["id"]: candidate for candidate in analysis["candidates"]}
            resolutions = db.execute("SELECT * FROM resolutions WHERE run_id=?", (row["id"],)).fetchall()
            decision_events = {event["id"] for event in events if event["action"] in {"resolve", "replay"}}
            if decision_events != {resolution["event_id"] for resolution in resolutions}:
                raise ValueError("Decision history is incomplete")
            for resolution in resolutions:
                event = event_map.get(resolution["event_id"])
                case = cases.get(resolution["case_id"])
                if event is None or case is None or event["case_id"] != case["id"]:
                    raise ValueError("Decision evidence mismatch")
                details = event["details"]
                for key in ("action", "candidate_id", "candidate_hash", "case_fingerprint"):
                    if details.get(key) != resolution[key]:
                        raise ValueError("Decision audit mismatch")
                if event["created_at"] != resolution["created_at"] or event["note"] != resolution["note"] or event["reviewer"] != resolution["reviewer"]:
                    raise ValueError("Decision attribution mismatch")
                if case["fingerprint"] != resolution["case_fingerprint"] or resolution["rule_version"] != row["rule_version"]:
                    raise ValueError("Decision rule mismatch")
                origin = json.loads(resolution["replayed_from_json"]) if resolution["replayed_from_json"] else None
                if origin != details.get("replayed_from"):
                    raise ValueError("Replay origin mismatch")
                if origin and (event["action"] != "replay" or origin["event_id"] != resolution["origin_event_id"] or origin["run_id"] != resolution["origin_run_id"]):
                    raise ValueError("Replay origin mismatch")
                if not origin and (event["action"] != "resolve" or resolution["origin_event_id"] or resolution["origin_run_id"]):
                    raise ValueError("Human decision origin mismatch")
                if resolution["action"] == "select":
                    candidate = candidates.get(resolution["candidate_id"])
                    if not candidate or candidate["id"] not in case["candidate_ids"] or not candidate["valid"]:
                        raise ValueError("Decision selects invalid candidate")
                    if candidate_hash(candidate) != resolution["candidate_hash"]:
                        raise ValueError("Selected evidence mismatch")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise StoreError(409, "Stored evidence or audit integrity check failed. Restore a trusted backup before continuing.") from exc

    def _append_event(self, db: sqlite3.Connection, run_id: str, action: str, reviewer: str,
                      note: str, details: dict, case_id: str | None = None) -> dict:
        last = db.execute("SELECT event_hash FROM audit WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run_id,)).fetchone()
        event = {"id": uuid.uuid4().hex, "run_id": run_id, "case_id": case_id, "action": action,
                 "reviewer": reviewer, "note": note, "created_at": now(), "details": details,
                 "previous_hash": last["event_hash"] if last else ""}
        event["event_hash"] = digest(canonical_json(event))
        db.execute("INSERT INTO audit(id,run_id,case_id,action,reviewer,note,created_at,details_json,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (event["id"], run_id, case_id, action, reviewer, note, event["created_at"],
                    canonical_json(details), event["previous_hash"], event["event_hash"]))
        return event

    @staticmethod
    def _fingerprint(scorer: str, league: str, rule_version: str = RULE_VERSION) -> str:
        return digest(canonical_json({"rule_version": rule_version, "scorer": digest(scorer), "league": digest(league)}))

    def create_run(self, name: str, scorer_csv: str, league_csv: str, is_demo: bool = False) -> dict:
        for label, content in (("Scorer", scorer_csv), ("League", league_csv)):
            try:
                source_size = len(content.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise StoreError(400, f"{label} source must be valid UTF-8 text.") from exc
            if source_size > MAX_SOURCE_BYTES:
                raise StoreError(413, f"{label} source exceeds the 1 MiB limit.")
        try:
            analysis = analyze(scorer_csv, league_csv)
        except ValueError as exc:
            raise StoreError(400, str(exc)) from exc
        analysis_text = canonical_json(analysis)
        fingerprint = self._fingerprint(scorer_csv, league_csv)
        with self.connection(write=True) as db:
            existing = db.execute("SELECT * FROM runs WHERE fingerprint=?", (fingerprint,)).fetchone()
            if existing:
                return self._detail(db, existing)
            run_id = uuid.uuid4().hex
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (run_id, name.strip(), now(), RULE_VERSION, fingerprint, scorer_csv,
                        league_csv, analysis_text, digest(analysis_text), int(is_demo)))
            self._append_event(db, run_id, "ingest", "system", "Imported immutable CSV evidence.",
                               {"fingerprint": fingerprint, "rule_version": RULE_VERSION,
                                "sources": analysis["sources"], "stats": analysis["stats"], "is_demo": is_demo})
            return self._detail(db, self._run(db, run_id))

    @staticmethod
    def _resolve_view(resolution: sqlite3.Row) -> dict:
        result = {key: resolution[key] for key in ("action", "candidate_id", "note", "reviewer", "created_at")}
        if resolution["replayed_from_json"]:
            result["replayed_from"] = json.loads(resolution["replayed_from_json"])
        return result

    @staticmethod
    def _selected_game(candidate: dict) -> dict:
        return {**candidate["normalized"], "provenance": [candidate["id"]]}

    def _state(self, db: sqlite3.Connection, row: sqlite3.Row) -> tuple[dict, list[dict], list[dict]]:
        analysis = json.loads(row["analysis_json"])
        candidates = {candidate["id"]: candidate for candidate in analysis["candidates"]}
        resolutions = {resolution["case_id"]: resolution for resolution in db.execute("SELECT * FROM resolutions WHERE run_id=?", (row["id"],))}
        cases = []
        canonical = list(analysis["canonical"])
        for original_case in analysis["cases"]:
            case = dict(original_case)
            resolution = resolutions.get(case["id"])
            case.update(status="resolved" if resolution else "open", version=1 if resolution else 0,
                        resolution=self._resolve_view(resolution) if resolution else None)
            if resolution and resolution["action"] == "select":
                canonical.append(self._selected_game(candidates[resolution["candidate_id"]]))
            cases.append(case)
        canonical.sort(key=lambda game: (game["season"], game["game_date"], game["game_id"], game["key"]))
        return analysis, cases, canonical

    def _prior_decision(self, db: sqlite3.Connection, row: sqlite3.Row, case: dict,
                        candidates: dict[str, dict], verified_runs: set[str] | None = None) -> tuple[sqlite3.Row, str | None] | None:
        # Replays never become new original human evidence. Prefer the latest
        # human decision when two earlier runs legitimately differ in judgment.
        priors = db.execute("SELECT * FROM resolutions WHERE case_fingerprint=? AND rule_version=? AND origin_event_id IS NULL AND run_id<>? ORDER BY created_at DESC,event_id DESC",
                            (case["fingerprint"], row["rule_version"], row["id"])).fetchall()
        for prior in priors:
            candidate_id = None
            if prior["action"] == "select":
                matching = [candidates[cid] for cid in case["candidate_ids"]
                            if candidates[cid]["valid"] and candidate_hash(candidates[cid]) == prior["candidate_hash"]]
                if not matching:
                    continue
                candidate_id = sorted(matching, key=lambda candidate: candidate["id"])[0]["id"]
            if verified_runs is None or prior["run_id"] not in verified_runs:
                self._assert_integrity(db, self._run(db, prior["run_id"]))
                if verified_runs is not None:
                    verified_runs.add(prior["run_id"])
            return prior, candidate_id
        return None

    def _detail(self, db: sqlite3.Connection, row: sqlite3.Row, include_full: bool = True) -> dict:
        self._assert_integrity(db, row)
        analysis, cases, canonical = self._state(db, row)
        open_count = sum(case["status"] == "open" for case in cases)
        excluded = sum(bool(case["resolution"] and case["resolution"]["action"] == "exclude") for case in cases)
        total = analysis["stats"]["game_count"]
        summary = {"id": row["id"], "name": row["name"], "created_at": row["created_at"],
                   "rule_version": row["rule_version"], "game_count": total,
                   "input_rows": analysis["stats"]["input_rows"], "open_cases": open_count,
                   "resolved_cases": len(cases) - open_count, "excluded_games": excluded,
                   "published_games": len(canonical), "auto_matched": analysis["stats"]["auto_matched"],
                   "quality_score": (100 * len(canonical) // total) if total else 0,
                   "is_demo": bool(row["is_demo"])}
        if not include_full:
            return summary
        candidate_map = {candidate["id"]: candidate for candidate in analysis["candidates"]}
        verified: set[str] = set()
        replay_available = sum(self._prior_decision(db, row, case, candidate_map, verified) is not None
                               for case in cases if case["status"] == "open")
        return {**summary, "sources": analysis["sources"], "candidates": analysis["candidates"],
                "cases": cases, "canonical": canonical, "standings": standings(canonical),
                "normalizations": analysis["normalizations"], "stats": analysis["stats"],
                "rules": analysis["rules"], "audit": self._events(db, row["id"]),
                "replay_available": replay_available}

    def list_runs(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY created_at DESC,id DESC").fetchall()
            return [self._detail(db, row, include_full=False) for row in rows]

    def get_run(self, run_id: str) -> dict:
        with self.connection() as db:
            return self._detail(db, self._run(db, run_id))

    def _case_context(self, db: sqlite3.Connection, run_id: str, case_id: str) -> tuple[sqlite3.Row, dict, dict, list[dict]]:
        row = self._run(db, run_id)
        self._assert_integrity(db, row)
        analysis, cases, canonical = self._state(db, row)
        case = next((case for case in cases if case["id"] == case_id), None)
        if case is None:
            raise StoreError(404, "Review case not found.")
        return row, analysis, case, canonical

    @staticmethod
    def _choose(analysis: dict, case: dict, action: str, candidate_id: str | None) -> dict | None:
        if action == "exclude":
            if candidate_id is not None:
                raise StoreError(422, "An exclusion must not select a candidate.")
            return None
        if action != "select":
            raise StoreError(422, "Action must be select or exclude.")
        if candidate_id not in case["candidate_ids"]:
            raise StoreError(422, "Select a candidate belonging to this review case.")
        candidate = next(candidate for candidate in analysis["candidates"] if candidate["id"] == candidate_id)
        if not candidate["valid"]:
            raise StoreError(422, "Invalid source rows cannot be published. Choose a valid source or quarantine the game.")
        return candidate

    def preview(self, run_id: str, case_id: str, action: str, candidate_id: str | None) -> dict:
        with self.connection() as db:
            _, analysis, case, canonical = self._case_context(db, run_id, case_id)
            if case["status"] != "open":
                raise StoreError(409, "This case is already resolved. Accepted decisions are immutable.")
            candidate = self._choose(analysis, case, action, candidate_id)
            selected = self._selected_game(candidate) if candidate else None
            after = canonical + ([selected] if selected else [])
            candidates = [c for c in analysis["candidates"] if c["id"] in case["candidate_ids"] and c["valid"]]
            candidates.sort(key=lambda c: (c["source"] != "scorer", c["row_number"], c["id"]))
            baseline = candidates[0]["normalized"] if candidates else {}
            fields = [field for field in ("season", "game_id", "game_date", "home_team", "away_team", "home_goals", "away_goals", "home_shots", "away_shots", "status", "venue", "updated_at")
                      if baseline.get(field) != (selected or {}).get(field)]
            return {"before": {"published_games": len(canonical), "standings": standings(canonical)},
                    "after": {"published_games": len(after), "standings": standings(after)},
                    "selected": selected, "excluded": candidate is None,
                    "changes": [{"field": field, "before": baseline.get(field), "after": (selected or {}).get(field)} for field in fields]}

    def _record_resolution(self, db: sqlite3.Connection, row: sqlite3.Row, case: dict, action: str,
                           candidate: dict | None, reviewer: str, note: str,
                           prior: sqlite3.Row | None = None) -> None:
        candidate_id = candidate["id"] if candidate else None
        evidence_hash = candidate_hash(candidate) if candidate else None
        replayed_from = {"run_id": prior["run_id"], "case_id": prior["case_id"], "event_id": prior["event_id"]} if prior else None
        details = {"action": action, "candidate_id": candidate_id, "candidate_hash": evidence_hash,
                   "case_fingerprint": case["fingerprint"], "rule_version": row["rule_version"],
                   "key": case["key"], "expected_version": 0, "version": 1}
        if replayed_from:
            details["replayed_from"] = replayed_from
            details["original_reviewer"] = prior["reviewer"]
        event = self._append_event(db, row["id"], "replay" if prior else "resolve", reviewer, note, details, case["id"])
        db.execute("INSERT INTO resolutions(run_id,case_id,case_fingerprint,rule_version,action,candidate_id,candidate_hash,note,reviewer,created_at,event_id,origin_event_id,origin_run_id,replayed_from_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (row["id"], case["id"], case["fingerprint"], row["rule_version"], action,
                    candidate_id, evidence_hash, note, reviewer, event["created_at"], event["id"],
                    prior["event_id"] if prior else None, prior["run_id"] if prior else None,
                    canonical_json(replayed_from) if replayed_from else None))

    def resolve(self, run_id: str, case_id: str, action: str, candidate_id: str | None,
                expected_version: int, note: str, reviewer: str) -> dict:
        with self.connection(write=True) as db:
            row, analysis, case, _ = self._case_context(db, run_id, case_id)
            if case["status"] != "open" or case["version"] != expected_version:
                raise StoreError(409, "Review version is stale or this case is already resolved. Refresh the run.")
            candidate = self._choose(analysis, case, action, candidate_id)
            self._record_resolution(db, row, case, action, candidate, reviewer, note)
            return self._detail(db, row)

    def replay(self, run_id: str, reviewer: str) -> dict:
        with self.connection(write=True) as db:
            row = self._run(db, run_id)
            self._assert_integrity(db, row)
            analysis, cases, _ = self._state(db, row)
            candidate_map = {candidate["id"]: candidate for candidate in analysis["candidates"]}
            verified: set[str] = set()
            applied = 0
            for case in cases:
                if case["status"] != "open":
                    continue
                match = self._prior_decision(db, row, case, candidate_map, verified)
                if match is None:
                    continue
                prior, candidate_id = match
                candidate = candidate_map[candidate_id] if candidate_id else None
                self._record_resolution(db, row, case, prior["action"], candidate, reviewer, prior["note"], prior=prior)
                applied += 1
            return {"applied": applied, "run": self._detail(db, row)}

    def export(self, run_id: str) -> tuple[bytes, str]:
        with self.connection() as db:
            row = self._run(db, run_id)
            detail = self._detail(db, row)
            if detail["open_cases"]:
                raise StoreError(409, f"Review required: resolve all {detail['open_cases']} open cases before export.")
            excluded = [{"key": case["key"], "case_id": case["id"], "reason": case["resolution"]["note"],
                         "reviewer": case["resolution"]["reviewer"]}
                        for case in detail["cases"] if case["resolution"] and case["resolution"]["action"] == "exclude"]
            files = {"canonical.csv": canonical_csv(detail["canonical"]).encode("utf-8"),
                     "audit.json": (json.dumps(detail["audit"], ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                     "raw_scorer.csv": row["scorer_csv"].encode("utf-8"),
                     "raw_league.csv": row["league_csv"].encode("utf-8")}
            manifest = {"schema_version": 1, "run_id": run_id, "name": row["name"],
                        "rule_version": row["rule_version"], "exported_at": now(), "is_demo": bool(row["is_demo"]),
                        "total_groups": detail["game_count"], "published_count": len(detail["canonical"]),
                        "excluded_count": len(excluded), "excluded_keys": [entry["key"] for entry in excluded],
                        "exclusions": excluded, "open_cases": 0,
                        "completeness_status": "partial_with_exclusions" if excluded else "complete",
                        "publishable_coverage_percent": detail["quality_score"],
                        "checksums": {name: digest(data) for name, data in files.items()},
                        "sources": detail["sources"],
                        "provenance": {game["key"]: game["provenance"] for game in detail["canonical"]},
                        "audit": {"event_count": len(detail["audit"]), "head_hash": detail["audit"][-1]["event_hash"],
                                  "integrity_checked": True, "download_logged": False,
                                  "scope": "Hash chain detects accidental or partial modification; it is not administrator-proof."},
                        "reviewer_identity": "Self-reported local operator labels; no authentication.",
                        "standings_policy": "Final games only: win 2 points, tie 1, loss 0.",
                        "csv_safety": "Canonical CSV escapes formula-like text. Raw CSVs preserve untrusted original bytes; inspect raw evidence in a text editor."}
            files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for filename, data in files.items():
                    archive.writestr(filename, data)
            return buffer.getvalue(), f"rinkcheck-{run_id[:12]}.zip"
