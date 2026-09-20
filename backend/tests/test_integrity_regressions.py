"""Cross-boundary integrity regressions independent of the bundled demo data.

These tests check the promises a reviewer relies on: unresolved/invalid evidence
cannot become published facts, changed evidence cannot inherit approval, and a
review cannot rewrite the evidence or previous decision history.
"""

import csv
import hashlib
import io
import json
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from rinkcheck.api import create_app
from rinkcheck.engine import analyze, canonical_csv
from rinkcheck.store import Store


FIELDS = (
    "season", "game_id", "game_date", "home_team", "away_team", "home_goals",
    "away_goals", "home_shots", "away_shots", "status", "venue", "updated_at",
)


def row(game_id="G-01", **changes):
    record = {
        "season": "2026-27", "game_id": game_id, "game_date": "2026-10-01",
        "home_team": "North Pines", "away_team": "Harbour Foxes",
        "home_goals": "3", "away_goals": "2", "home_shots": "25",
        "away_shots": "21", "status": "final", "venue": "River Rink",
        "updated_at": "2026-10-01T21:00:00Z",
    }
    record.update(changes)
    return record


def feed(*records, extra_fields=()):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=(*FIELDS, *extra_fields))
    writer.writeheader()
    writer.writerows(records)
    return output.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "review.db", seed=False)) as app:
        yield app


def import_run(client, scorer, league, name="Regression release"):
    response = client.post("/api/runs", json={
        "name": name, "scorer_csv": scorer, "league_csv": league,
    })
    assert response.is_success, response.text
    return response.json()


def resolve_case(client, run, case, action="select", candidate_id=None):
    response = client.post(f"/api/runs/{run['id']}/cases/{case['id']}/resolve", json={
        "action": action, "candidate_id": candidate_id,
        "expected_version": case["version"], "reviewer": "Regression reviewer",
        "note": "Verified against the original scorer record.",
    })
    assert response.is_success, response.text
    return response.json()


def test_fingerprint_tracks_raw_evidence_but_not_csv_row_positions():
    left = [row("G-01", operator_note="review A"), row("G-02", operator_note="review B")]
    right = [row("G-01", home_goals="4"), row("G-02", home_goals="5")]
    original = analyze(feed(*left, extra_fields=("operator_note",)), feed(*right))
    reordered = analyze(feed(*reversed(left), extra_fields=("operator_note",)), feed(*reversed(right)))
    def fingerprints(result):
        return {case["key"]: case["fingerprint"] for case in result["cases"]}
    assert fingerprints(original) == fingerprints(reordered)

    left[0]["operator_note"] = "review A amended after approval"
    changed = analyze(feed(*left, extra_fields=("operator_note",)), feed(*right))
    previous, current = fingerprints(original), fingerprints(changed)
    assert sum(previous[key] != current[key] for key in previous) == 1
    assert all("operator_note" not in candidate["normalized"] for candidate in changed["candidates"])


def test_invalid_duplicate_cannot_hide_behind_two_matching_valid_records():
    result = analyze(feed(row(), row(home_goals="invalid")), feed(row()))
    assert result["canonical"] == []
    assert len(result["cases"]) == 1
    case = result["cases"][0]
    assert len(case["candidate_ids"]) == 3
    assert "home_goals" in case["fields"]
    assert any(not item["valid"] for item in result["candidates"])


def test_missing_identifiers_do_not_falsely_join_across_sources():
    result = analyze(feed(row(game_id="")), feed(row(game_id="")))
    assert result["canonical"] == []
    assert len(result["cases"]) == 2
    assert len({case["key"] for case in result["cases"]}) == 2


@pytest.mark.parametrize("formula", ["=1+1", "+SUM(1,1)", "@SUM(1,1)", "-1+1", "＝1+1"])
def test_canonical_csv_neutralizes_spreadsheet_formulas(formula):
    result = analyze(feed(row(venue=formula)), feed(row(venue=formula)))
    assert not result["cases"]
    exported = list(csv.DictReader(io.StringIO(canonical_csv(result["canonical"]))))
    assert len(exported) == 1
    assert exported[0]["venue"].startswith("'"), "Spreadsheet-sensitive text must be literalized"
    assert exported[0]["home_goals"] == "3", "Actual numeric facts must remain numeric CSV values"


def test_export_is_blocked_and_invalid_selection_cannot_create_approval(client):
    run = import_run(client, feed(row(home_goals="3.0")), feed(row(home_goals="4")))
    case = run["cases"][0]
    invalid = next(item for item in run["candidates"] if not item["valid"])
    original_audit = run["audit"]
    assert client.get(f"/api/runs/{run['id']}/export").status_code == 409
    response = client.post(f"/api/runs/{run['id']}/cases/{case['id']}/resolve", json={
        "action": "select", "candidate_id": invalid["id"], "expected_version": 0,
        "reviewer": "Regression reviewer", "note": "Attempt invalid candidate selection.",
    })
    assert response.status_code in (400, 422), response.text
    latest = client.get(f"/api/runs/{run['id']}").json()
    assert latest["open_cases"] == 1
    assert latest["canonical"] == []
    assert latest["audit"] == original_audit


def test_ingestion_failure_does_not_create_a_partial_run(client):
    import_run(client, feed(row()), feed(row()))
    before = client.get("/api/runs").json()
    malformed = feed(row()) + "2026-27,G-02,too,many,cells,in,this,row,0,0,0,0,0,0,0\n"
    response = client.post("/api/runs", json={
        "name": "Should not exist", "scorer_csv": feed(row("G-99")), "league_csv": malformed,
    })
    assert response.status_code in (400, 422), response.text
    assert client.get("/api/runs").json() == before


def test_candidate_from_another_case_cannot_resolve_this_case(client):
    run = import_run(client, feed(row("G-01"), row("G-02")),
                     feed(row("G-01", home_goals="4"), row("G-02", home_goals="5")))
    first, second = run["cases"]
    foreign_candidate = second["candidate_ids"][0]
    response = client.post(f"/api/runs/{run['id']}/cases/{first['id']}/resolve", json={
        "action": "select", "candidate_id": foreign_candidate, "expected_version": 0,
        "reviewer": "Regression reviewer", "note": "Wrong game's candidate must be rejected.",
    })
    assert response.status_code in (400, 422), response.text
    assert client.get(f"/api/runs/{run['id']}").json() == run


def test_preview_cannot_write_history_and_stale_review_cannot_replace_decision(client):
    run = import_run(client, feed(row()), feed(row(home_goals="4")))
    case = run["cases"][0]
    selected = next(item for item in run["candidates"] if item["source"] == "league")
    payload = {"action": "select", "candidate_id": selected["id"]}
    preview = client.post(f"/api/runs/{run['id']}/cases/{case['id']}/preview", json=payload)
    assert preview.is_success, preview.text
    assert client.get(f"/api/runs/{run['id']}").json() == run
    accepted = resolve_case(client, run, case, candidate_id=selected["id"])
    response = client.post(f"/api/runs/{run['id']}/cases/{case['id']}/resolve", json={
        "action": "exclude", "expected_version": 0, "reviewer": "Different reviewer",
        "note": "A stale browser tried overwriting the review.",
    })
    assert response.status_code == 409
    current = client.get(f"/api/runs/{run['id']}").json()
    assert current == accepted
    assert current["canonical"][0]["home_goals"] == 4


def test_replay_rebinds_reordered_candidate_and_refuses_changed_raw_evidence(client):
    scorer = [row("G-01", operator_note="scorer A"), row("G-02", operator_note="scorer B")]
    league = [row("G-01", home_goals="4"), row("G-02", home_goals="5")]
    run = import_run(client, feed(*scorer, extra_fields=("operator_note",)), feed(*league))
    for case in run["cases"]:
        candidate = next(item for item in run["candidates"]
                         if item["id"] in case["candidate_ids"] and item["source"] == "league")
        resolve_case(client, run, case, candidate_id=candidate["id"])

    # Raw CSV order is new, but each game's complete evidence is identical.
    reordered = import_run(client, feed(*reversed(scorer), extra_fields=("operator_note",)), feed(*reversed(league)))
    response = client.post(f"/api/runs/{reordered['id']}/replay", json={"reviewer": "Release reviewer"})
    assert response.is_success, response.text
    replayed = response.json()
    assert replayed["applied"] == 2
    assert {game["game_id"]: game["home_goals"] for game in replayed["run"]["canonical"]} == {"G-01": 4, "G-02": 5}

    # Even changing an ignored extra column invalidates the earlier approval.
    scorer[0]["operator_note"] = "Correction received after review"
    changed = import_run(client, feed(*scorer, extra_fields=("operator_note",)), feed(*league))
    response = client.post(f"/api/runs/{changed['id']}/replay", json={"reviewer": "Release reviewer"})
    assert response.is_success, response.text
    replayed = response.json()
    assert replayed["applied"] == 1
    assert replayed["run"]["open_cases"] == 1
    assert {game["game_id"] for game in replayed["run"]["canonical"]} == {"G-02"}
    remaining = next(case for case in replayed["run"]["cases"] if case["status"] == "open")
    assert any(item["normalized"]["game_id"] == "G-01" for item in changed["candidates"] if item["id"] in remaining["candidate_ids"])


def test_audit_and_raw_evidence_cannot_be_updated_or_deleted(tmp_path):
    path = tmp_path / "immutable.db"
    with TestClient(create_app(db_path=path, seed=False)) as client:
        run = import_run(client, feed(row()), feed(row(home_goals="4")))
        candidate = run["candidates"][0]
        resolve_case(client, run, run["cases"][0], candidate_id=candidate["id"])
        for query in (
            "UPDATE runs SET scorer_csv = 'rewritten'",
            "DELETE FROM runs",
            "UPDATE audit SET note = 'rewritten'",
            "DELETE FROM audit",
            "UPDATE resolutions SET note = 'rewritten'",
            "DELETE FROM resolutions",
        ):
            with sqlite3.connect(path) as conn, pytest.raises(sqlite3.IntegrityError):
                conn.execute(query)


def test_export_declares_exclusions_and_preserves_checked_source_evidence(client):
    scorer = feed(row("G-01"), row("G-02", venue="=1+1"))
    league = feed(row("G-01", home_goals="4"))
    run = import_run(client, scorer, league)
    for case in run["cases"]:
        if case["kind"] == "missing":
            excluded_key = case["key"]
            resolve_case(client, run, case, action="exclude")
        else:
            candidate = next(item for item in run["candidates"]
                             if item["id"] in case["candidate_ids"] and item["source"] == "league")
            selected_id = candidate["id"]
            resolve_case(client, run, case, candidate_id=selected_id)
    latest = client.get(f"/api/runs/{run['id']}").json()
    assert latest["quality_score"] == 50, "Excluded games must remain in the coverage denominator"
    response = client.get(f"/api/runs/{run['id']}/export")
    assert response.is_success, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        assert set(bundle.namelist()) == {
            "canonical.csv", "manifest.json", "audit.json", "raw_scorer.csv", "raw_league.csv",
        }
        manifest = json.loads(bundle.read("manifest.json"))
        exported = list(csv.DictReader(io.StringIO(bundle.read("canonical.csv").decode())))
        audit = json.loads(bundle.read("audit.json"))
        assert [(item["game_id"], item["home_goals"]) for item in exported] == [("G-01", "4")]
        assert manifest["completeness_status"] == "partial_with_exclusions"
        assert manifest["total_groups"] == 2
        assert manifest["published_count"] == 1
        assert manifest["excluded_keys"] == [excluded_key]
        assert manifest["publishable_coverage_percent"] == 50
        assert next(iter(manifest["provenance"].values())) == [selected_id]
        assert bundle.read("raw_scorer.csv") == scorer.encode()
        assert bundle.read("raw_league.csv") == league.encode()
        for filename, expected_hash in manifest["checksums"].items():
            assert hashlib.sha256(bundle.read(filename)).hexdigest() == expected_hash
        assert audit == latest["audit"]
        assert manifest["audit"]["download_logged"] is False
    assert client.get(f"/api/runs/{run['id']}").json()["audit"] == audit


def test_ingestion_and_resolution_roll_back_when_audit_append_fails(tmp_path, monkeypatch):
    store = Store(tmp_path / "atomic.db")
    original_append = store._append_event

    def append_then_fail(*args, **kwargs):
        original_append(*args, **kwargs)
        raise RuntimeError("Simulated failure after writing an audit event")

    with monkeypatch.context() as context:
        context.setattr(store, "_append_event", append_then_fail)
        with pytest.raises(RuntimeError, match="Simulated failure"):
            store.create_run("Broken import", feed(row()), feed(row(home_goals="4")))
    assert store.list_runs() == []

    run = store.create_run("Successful import", feed(row()), feed(row(home_goals="4")))
    case = run["cases"][0]
    with monkeypatch.context() as context:
        context.setattr(store, "_append_event", append_then_fail)
        with pytest.raises(RuntimeError, match="Simulated failure"):
            store.resolve(run["id"], case["id"], "select", case["candidate_ids"][0],
                          0, "A valid reviewed choice.", "Regression reviewer")
    assert store.get_run(run["id"]) == run
