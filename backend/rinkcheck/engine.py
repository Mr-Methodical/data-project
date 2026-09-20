"""Pure, deterministic hockey CSV reconciliation.

The boundary is intentionally strict: malformed CSV rejects the entire import;
bad *records* remain inspectable review evidence. This module does no I/O, makes
no network calls, and never modifies either source.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone

from .rules import COLUMNS, COMPARISON_FIELDS, RULES, RULE_VERSION, STAT_FIELDS, TEAM_ALIASES

MAX_BYTES = 1_048_576
MAX_ROWS = 5_000
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_INT_RE = re.compile(r"[0-9]+\Z")
_SEASON_RE = re.compile(r"([0-9]{4})-([0-9]{2})\Z")
_TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z")


def _stable(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def candidate_evidence(candidate: dict) -> dict:
    """Identity for replay: source + ALL raw cells, independent of CSV position.

    Raw values deliberately include extra named columns. A changed annotation,
    timestamp, formatting, or normalization history invalidates a prior review.
    Row numbers, assigned candidate IDs and provenance are not evidence content.
    """
    return {"source": candidate["source"], "raw": candidate["raw"]}


def candidate_evidence_hash(candidate: dict) -> str:
    return _digest({"rule_version": RULE_VERSION, "evidence": candidate_evidence(candidate)})


def _clean(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").split())


def _normalize(raw: dict, source: str, row_number: int) -> dict:
    candidate_id = f"{source}:{row_number}"
    changes: list[dict] = []
    errors: list[dict] = []
    values: dict = {}

    def issue(rule_id: str, field: str, message: str) -> None:
        errors.append({"rule_id": rule_id, "field": field, "message": message})

    for field in COLUMNS:
        before = raw.get(field)
        after = _clean(before)
        if before is not None and before != after:
            changes.append({"field": field, "before": before, "after": after,
                            "reason": "Unicode NFKC and whitespace normalization"})
        values[field] = after

    for field in ("home_team", "away_team"):
        before = values[field]
        after = TEAM_ALIASES.get(before, before)
        if before != after:
            changes.append({"field": field, "before": before, "after": after,
                            "reason": "Explicit team alias"})
            values[field] = after
        if not after:
            issue("ROW_INVALID", field, "Team name is required.")
    if values["home_team"] and values["home_team"] == values["away_team"]:
        issue("TEAM_COLLISION", "away_team", "Home and away teams normalize to the same team.")

    season_match = _SEASON_RE.fullmatch(values["season"])
    if not season_match or (int(season_match[1]) + 1) % 100 != int(season_match[2]):
        issue("IDENTITY_INVALID", "season", "Season must be consecutive YYYY-YY, for example 2026-27.")
    if not _ID_RE.fullmatch(values["game_id"]):
        issue("IDENTITY_INVALID", "game_id", "Game ID must be 1–64 letters/digits/dots/underscores/hyphens, starting with a letter or digit.")

    try:
        if not _DATE_RE.fullmatch(values["game_date"]):
            raise ValueError
        date.fromisoformat(values["game_date"])
    except ValueError:
        issue("ROW_INVALID", "game_date", "Game date must be a real ISO YYYY-MM-DD date.")

    status = values["status"]
    if status.lower() in {"final", "scheduled"}:
        values["status"] = status.lower()
        if status != values["status"]:
            changes.append({"field": "status", "before": status, "after": values["status"],
                            "reason": "Canonical status spelling"})
    else:
        issue("ROW_INVALID", "status", "Status must be final or scheduled.")

    supplied_stats = sum(values[field] != "" for field in STAT_FIELDS)
    if values["status"] == "scheduled" and supplied_stats not in {0, 4}:
        issue("ROW_INVALID", "status", "Scheduled rows must provide all four statistics or leave all four empty.")
    for field in STAT_FIELDS:
        value = values[field]
        if not value:
            values[field] = None
            if values["status"] == "final":
                issue("ROW_INVALID", field, "Final games require a nonnegative integer statistic.")
        elif not _INT_RE.fullmatch(value) or len(value) > 9:
            values[field] = None
            issue("ROW_INVALID", field, "Statistic must be a nonnegative integer with at most nine digits (no decimals, signs or NaN).")
        else:
            values[field] = int(value)
            if str(values[field]) != value:
                changes.append({"field": field, "before": value, "after": str(values[field]),
                                "reason": "Canonical integer spelling"})
    for side in ("home", "away"):
        goals, shots = values[f"{side}_goals"], values[f"{side}_shots"]
        if goals is not None and shots is not None and goals > shots:
            issue("SCORE_EXCEEDS_SHOTS", f"{side}_shots", f"{side.title()} shots ({shots}) cannot be lower than goals ({goals}).")
    if not values["venue"]:
        issue("ROW_INVALID", "venue", "Venue is required.")

    try:
        if not _TIME_RE.fullmatch(values["updated_at"]):
            raise ValueError
        timestamp = datetime.fromisoformat(values["updated_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
        # UTC serialization is documented normalization, never freshness voting.
        canonical_time = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if canonical_time != values["updated_at"]:
            changes.append({"field": "updated_at", "before": values["updated_at"], "after": canonical_time,
                            "reason": "Equivalent timestamp expressed in UTC"})
            values["updated_at"] = canonical_time
    except (ValueError, OverflowError):
        issue("ROW_INVALID", "updated_at", "Timestamp must be ISO8601 with seconds and an explicit timezone.")

    identity_invalid = any(error["rule_id"] == "IDENTITY_INVALID" for error in errors)
    key = f"invalid:{source}:{row_number}" if identity_invalid else f"{values['season']}/{values['game_id']}"
    values = {"key": key, **values, "provenance": [candidate_id]}
    return {"id": candidate_id, "source": source, "row_number": row_number, "key": key,
            "raw": raw, "normalized": values, "errors": errors, "normalizations": changes,
            "valid": not errors}


def _parse(content: str, source: str) -> tuple[dict, list[dict]]:
    if not isinstance(content, str):
        raise ValueError(f"{source}: CSV content must be UTF-8 text.")
    try:
        encoded = content.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError(f"{source}: invalid UTF-8 text.") from exc
    if len(encoded) > MAX_BYTES:
        raise ValueError(f"{source}: file exceeds 1 MiB.")
    if not content.strip():
        raise ValueError(f"{source}: CSV is empty.")
    if "\x00" in content:
        raise ValueError(f"{source}: NUL bytes are not accepted in CSV text.")
    candidates = []
    try:
        reader = csv.reader(io.StringIO(content.lstrip("\ufeff"), newline=""), strict=True)
        headers = next(reader)
        if not headers or any(not header.strip() for header in headers):
            raise ValueError(f"{source}: every column must have a name.")
        if len(headers) != len(set(headers)):
            raise ValueError(f"{source}: duplicate CSV headers.")
        missing = sorted(set(COLUMNS) - set(headers))
        if missing:
            raise ValueError(f"{source}: missing required headers: {', '.join(missing)}.")
        for values in reader:
            # Blank physical lines are CSV separators, not silent invalid records.
            if not values:
                continue
            if len(candidates) >= MAX_ROWS:
                raise ValueError(f"{source}: file exceeds {MAX_ROWS:,} data rows.")
            if len(values) > len(headers):
                raise ValueError(f"{source}: row ending at line {reader.line_num} has extra CSV cells.")
            row_number = reader.line_num
            raw = {header: values[index] if index < len(values) else None for index, header in enumerate(headers)}
            candidate = _normalize(raw, source, row_number)
            if len(values) < len(headers):
                # Even if only an optional cell is absent, do not silently repair
                # a truncated row. It remains selectable only after source repair.
                candidate["errors"].append({"rule_id": "ROW_INVALID", "field": "row",
                                            "message": "Row has fewer cells than named columns."})
                candidate["valid"] = False
            candidates.append(candidate)
        if not candidates:
            raise ValueError(f"{source}: CSV contains no data rows.")
    except (csv.Error, StopIteration) as exc:
        raise ValueError(f"{source}: malformed CSV: {exc}.") from exc
    return {"id": source, "label": "Official scorer" if source == "scorer" else "League export",
            "row_count": len(candidates), "sha256": hashlib.sha256(encoded).hexdigest()}, candidates


def _comparison(candidate: dict) -> str:
    # Raw invalid fields are included so two different invalid values cannot
    # collapse merely because both normalized to null.
    return _stable({"values": {field: candidate["normalized"][field] for field in COMPARISON_FIELDS},
                    "invalid": [{**error, "raw": candidate["raw"].get(error["field"])} for error in candidate["errors"]]})


def analyze(scorer_csv: str, league_csv: str) -> dict:
    sources, candidates = [], []
    for source, content in (("scorer", scorer_csv), ("league", league_csv)):
        summary, parsed = _parse(content, source)
        sources.append(summary)
        candidates.extend(parsed)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate["key"]].append(candidate)

    cases, canonical = [], []
    exact_duplicates = 0
    for key in sorted(grouped):
        group = grouped[key]
        by_source = {source: [c for c in group if c["source"] == source] for source in ("scorer", "league")}
        rule_ids = {error["rule_id"] for c in group for error in c["errors"]}
        fields = {error["field"] for c in group for error in c["errors"]}
        if any(not rows for rows in by_source.values()):
            rule_ids.add("MISSING_SOURCE")
        for rows in by_source.values():
            signatures = [_comparison(c) for c in rows]
            # This metric is literal repeated evidence, not merely equivalent
            # normalized values or results bearing different timestamps.
            raw_signatures = [_stable(c["raw"]) for c in rows]
            exact_duplicates += len(raw_signatures) - len(set(raw_signatures))
            if len(set(signatures)) > 1:
                rule_ids.add("CONFLICTING_DUPLICATE")
        # Include every differing substantive field, even in invalid groups.
        for field in COMPARISON_FIELDS:
            normalized_values = {_stable(c["normalized"][field]) for c in group}
            if len(normalized_values) > 1:
                fields.add(field)
        left = {_comparison(c) for c in by_source["scorer"]}
        right = {_comparison(c) for c in by_source["league"]}
        if left and right and left != right:
            rule_ids.add("FIELD_CONFLICT")
        if not rule_ids:
            # Deterministic content selection independent of input row order.
            # Prefer scorer, then stable raw evidence; timestamps never decide truth.
            chosen = min(group, key=lambda c: (c["source"] != "scorer", _stable(candidate_evidence(c))))
            game = copy.deepcopy(chosen["normalized"])
            game["provenance"] = sorted(c["id"] for c in group)
            canonical.append(game)
            continue

        invalid = any(not c["valid"] for c in group)
        if invalid:
            kind, title = "invalid", "Invalid source data"
        elif "CONFLICTING_DUPLICATE" in rule_ids:
            kind, title = "duplicate", "Conflicting records in one source"
        elif "FIELD_CONFLICT" in rule_ids:
            kind, title = "conflict", "Sources disagree"
        else:
            kind, title = "missing", "Missing from one source"
        severity = "warning" if rule_ids == {"MISSING_SOURCE"} else "critical"
        evidence = sorted((candidate_evidence(c) for c in group), key=_stable)
        fingerprint = _digest({"rule_version": RULE_VERSION, "evidence": evidence})
        finding_titles = [r["title"] for r in RULES if r["id"] in rule_ids]
        cases.append({"id": "case-" + _digest({"key": key})[:16], "key": key,
                      "title": title, "kind": kind, "severity": severity, "rule_ids": sorted(rule_ids),
                      "description": "; ".join(finding_titles) + ". Review the original evidence before selecting a whole record or excluding the game.",
                      "fields": sorted(fields), "candidate_ids": [c["id"] for c in group],
                      "fingerprint": fingerprint, "status": "open", "version": 0, "resolution": None})
    normalizations = [{"source": c["source"], "row_number": c["row_number"], **change}
                      for c in candidates for change in c["normalizations"]]
    return {"rule_version": RULE_VERSION, "sources": sources, "candidates": candidates,
            "cases": cases, "canonical": canonical,
            "stats": {"input_rows": len(candidates), "game_count": len(grouped), "auto_matched": len(canonical),
                      "normalization_count": len(normalizations), "exact_duplicates": exact_duplicates},
            "normalizations": normalizations, "rules": copy.deepcopy(RULES)}


def standings(games: list[dict]) -> list[dict]:
    """Demo standings policy: two points for a win, one for a tie, zero for loss."""
    result: dict[str, dict] = {}
    seen: set[str] = set()
    for game in games:
        if game["status"] != "final":
            continue
        if game["key"] in seen:
            raise ValueError("Standings require unique canonical game keys.")
        seen.add(game["key"])
        for side, opposite in (("home", "away"), ("away", "home")):
            name = game[f"{side}_team"]
            own, against = game[f"{side}_goals"], game[f"{opposite}_goals"]
            if type(own) is not int or type(against) is not int or own < 0 or against < 0:
                raise ValueError("Standings require validated nonnegative integer final scores.")
            team = result.setdefault(name, {"team": name, "played": 0, "wins": 0, "losses": 0,
                                           "ties": 0, "goals_for": 0, "goals_against": 0,
                                           "goal_difference": 0, "points": 0})
            team["played"] += 1
            team["goals_for"] += own
            team["goals_against"] += against
            team["wins"] += int(own > against)
            team["losses"] += int(own < against)
            team["ties"] += int(own == against)
            team["points"] += 2 if own > against else 1 if own == against else 0
            team["goal_difference"] = team["goals_for"] - team["goals_against"]
    return sorted(result.values(), key=lambda t: (-t["points"], -t["goal_difference"], -t["goals_for"], t["team"]))


def _safe_cell(value: object) -> object:
    # Spreadsheet programs may treat leading whitespace followed by a formula
    # marker as executable. Prefix an apostrophe; quote_all alone is insufficient.
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
            return "'" + value
    return "" if value is None else value


def canonical_csv(games: list[dict]) -> str:
    """Stable export with spreadsheet formula neutralization in string cells."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for game in sorted(games, key=lambda g: (g["season"], g["game_id"], g["game_date"])):
        writer.writerow({field: _safe_cell(game[field]) for field in COLUMNS})
    return output.getvalue()
