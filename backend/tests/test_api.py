"""Exercise real HTTP routes against isolated, disk-backed SQLite databases."""
import hashlib
import io
import json
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from rinkcheck.api import MAX_BODY_BYTES, create_app
from rinkcheck.demo import demo_pair
from rinkcheck.store import MAX_SOURCE_BYTES, canonical_json


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "rinkcheck.sqlite3"


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path, seed=False)) as client:
        yield client


def load_demo(client, variant="opening"):
    response = client.post("/api/demo", json={"variant": variant})
    assert response.status_code == 200, response.text
    return response.json()


def decision(run, case, action="select"):
    candidate_id = next((c["id"] for c in run["candidates"] if c["id"] in case["candidate_ids"] and c["valid"]), None)
    result = {"action": action, "expected_version": case["version"],
              "reviewer": "Test reviewer", "note": "Checked original scorer report."}
    if action == "select" and candidate_id:
        result["candidate_id"] = candidate_id
    elif not candidate_id:
        result["action"] = "exclude"
    return result


def resolve_all(client, run, exclude_first=False):
    for index, case in enumerate(run["cases"]):
        if case["status"] != "open":
            continue
        payload = decision(run, case, "exclude" if exclude_first and index == 0 else "select")
        response = client.post(f"/api/runs/{run['id']}/cases/{case['id']}/resolve", json=payload)
        assert response.status_code == 200, response.text
        run = response.json()
    return run


def test_creation_is_idempotent_and_restart_preserves_reviews(client, db_path):
    assert client.get("/api/runs").json() == {"runs": []}
    run = load_demo(client)
    assert run["is_demo"] is True
    assert len(run["audit"]) == 1
    assert load_demo(client)["id"] == run["id"]
    assert len(client.get("/api/runs").json()["runs"]) == 1
    case = run["cases"][0]
    response = client.post(f"/api/runs/{run['id']}/cases/{case['id']}/resolve", json=decision(run, case))
    assert response.status_code == 200
    # Seeding an existing database never resets accepted reviews.
    with TestClient(create_app(db_path, seed=True)) as restarted:
        saved = restarted.get(f"/api/runs/{run['id']}").json()
        assert saved["resolved_cases"] == 1
        assert saved["cases"][0]["version"] == 1
        assert len(saved["audit"]) == 2
        assert len(restarted.get("/api/runs").json()["runs"]) == 1


def test_seed_only_creates_opening_for_empty_database(db_path):
    with TestClient(create_app(db_path, seed=True)) as client:
        first = client.get("/api/runs").json()["runs"]
        assert len(first) == 1
        assert first[0]["open_cases"] > 0
    with TestClient(create_app(db_path, seed=True)) as client:
        assert client.get("/api/runs").json()["runs"] == first


def test_rejected_input_never_creates_run(client):
    _, league = demo_pair("clean")
    response = client.post("/api/runs", json={"name": "Broken", "scorer_csv": "not,a,valid,header\n", "league_csv": league})
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)
    assert client.get("/api/runs").json()["runs"] == []
    response = client.post("/api/runs", json={"name": "Too large", "scorer_csv": "x" * (MAX_SOURCE_BYTES + 1), "league_csv": league})
    assert response.status_code == 413
    response = client.post("/api/runs", content=b"x" * (MAX_BODY_BYTES + 1), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    # The server must measure actual streamed bytes, not only Content-Length.
    response = client.post("/api/runs", content=iter([b"x" * MAX_BODY_BYTES, b"x"]), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert client.get("/api/runs").json()["runs"] == []


def test_validation_and_unknown_routes_return_string_detail(client):
    for response in [client.post("/api/demo", json={"variant": "nonsense"}),
                     client.post("/api/runs", json={"name": "", "scorer_csv": "", "league_csv": ""}),
                     client.get("/api/runs/unknown"), client.get("/api/unknown"),
                     client.post("/api/demo", json={"variant": "clean", "ignored": True})]:
        assert response.status_code in {404, 422}
        assert isinstance(response.json()["detail"], str)


def test_preview_does_not_mutate_and_selection_is_validated(client):
    run = load_demo(client)
    case = next(case for case in run["cases"] if sum(c["valid"] for c in run["candidates"] if c["id"] in case["candidate_ids"]) >= 1)
    payload = decision(run, case)
    endpoint = f"/api/runs/{run['id']}/cases/{case['id']}"
    preview = client.post(endpoint + "/preview", json={key: payload[key] for key in ("action", "candidate_id")}).json()
    assert preview["after"]["published_games"] == preview["before"]["published_games"] + 1
    assert preview["selected"]["provenance"] == [payload["candidate_id"]]
    assert client.get(f"/api/runs/{run['id']}").json() == run
    unrelated = next(c["id"] for c in run["candidates"] if c["id"] not in case["candidate_ids"])
    assert client.post(endpoint + "/resolve", json={**payload, "candidate_id": unrelated}).status_code == 422
    assert client.post(endpoint + "/resolve", json={**payload, "expected_version": 1}).status_code == 409
    assert client.post(endpoint + "/resolve", json={**payload, "expected_version": True}).status_code == 422
    assert client.post(endpoint + "/resolve", json={**payload, "note": "short"}).status_code == 422
    assert client.post(endpoint + "/resolve", json={**payload, "reviewer": "   "}).status_code == 422
    assert client.post(endpoint + "/resolve", json={**payload, "action": "exclude"}).status_code == 422
    assert client.get(f"/api/runs/{run['id']}").json() == run
    invalid = next(c for c in run["candidates"] if not c["valid"])
    invalid_case = next(c for c in run["cases"] if invalid["id"] in c["candidate_ids"])
    response = client.post(f"/api/runs/{run['id']}/cases/{invalid_case['id']}/resolve",
                           json={**payload, "candidate_id": invalid["id"]})
    assert response.status_code == 422


def test_competing_reviews_accept_exactly_one_transaction(client):
    run = load_demo(client)
    case = run["cases"][0]
    endpoint = f"/api/runs/{run['id']}/cases/{case['id']}/resolve"
    payload = decision(run, case)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(endpoint, json=payload), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    persisted = client.get(f"/api/runs/{run['id']}").json()
    assert persisted["resolved_cases"] == 1
    assert len(persisted["audit"]) == 2
    assert client.post(endpoint, json={**payload, "expected_version": 1}).status_code == 409


def test_export_gate_checksums_lineage_and_partial_coverage(client):
    run = load_demo(client)
    endpoint = f"/api/runs/{run['id']}/export"
    assert client.get(endpoint).status_code == 409
    run = resolve_all(client, run, exclude_first=True)
    response = client.get(endpoint)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert run["excluded_games"] >= 1
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"canonical.csv", "manifest.json", "audit.json", "raw_scorer.csv", "raw_league.csv"}
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["completeness_status"] == "partial_with_exclusions"
        assert manifest["published_count"] + manifest["excluded_count"] == manifest["total_groups"]
        assert manifest["published_count"] == run["published_games"]
        assert manifest["excluded_keys"]
        assert len(manifest["provenance"]) == manifest["published_count"]
        assert manifest["audit"]["download_logged"] is False
        for name, sha256 in manifest["checksums"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == sha256
        scorer, league = demo_pair("opening")
        assert archive.read("raw_scorer.csv") == scorer.encode("utf-8")
        assert archive.read("raw_league.csv") == league.encode("utf-8")
        events = json.loads(archive.read("audit.json"))
        previous = ""
        for event in events:
            assert event["previous_hash"] == previous
            body = {key: value for key, value in event.items() if key != "event_hash"}
            assert hashlib.sha256(canonical_json(body).encode()).hexdigest() == event["event_hash"]
            previous = event["event_hash"]
        assert previous == manifest["audit"]["head_hash"]
    assert client.get(f"/api/runs/{run['id']}").json()["audit"] == run["audit"]


def test_clean_export_is_complete_without_human_review(client):
    run = load_demo(client, "clean")
    assert run["open_cases"] == 0
    assert run["quality_score"] == 100
    response = client.get(f"/api/runs/{run['id']}/export")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["completeness_status"] == "complete"
    assert manifest["excluded_keys"] == []


def test_replay_reuses_only_exact_prior_evidence_and_retains_origin(client):
    original = resolve_all(client, load_demo(client))
    followup = load_demo(client, "followup")
    assert 0 < followup["replay_available"] < followup["open_cases"]
    response = client.post(f"/api/runs/{followup['id']}/replay", json={"reviewer": "Followup reviewer"})
    assert response.status_code == 200
    replay = response.json()
    assert replay["applied"] == followup["replay_available"]
    assert replay["run"]["open_cases"] > 0
    for event in replay["run"]["audit"]:
        if event["action"] == "replay":
            assert event["details"]["replayed_from"]["run_id"] == original["id"]
            assert event["details"]["replayed_from"]["event_id"] in {e["id"] for e in original["audit"]}
            assert event["details"]["original_reviewer"] == "Test reviewer"
    repeated = client.post(f"/api/runs/{followup['id']}/replay", json={"reviewer": "Followup reviewer"}).json()
    assert repeated["applied"] == 0
    assert repeated["run"]["audit"] == replay["run"]["audit"]


def test_database_guards_prevent_mutating_evidence_decisions_and_audit(client, db_path):
    run = load_demo(client)
    case = run["cases"][0]
    assert client.post(f"/api/runs/{run['id']}/cases/{case['id']}/resolve", json=decision(run, case)).status_code == 200
    with sqlite3.connect(db_path) as db:
        for sql in ["UPDATE runs SET scorer_csv='tampered'", "DELETE FROM runs", "UPDATE audit SET note='tampered'",
                    "DELETE FROM audit", "UPDATE resolutions SET note='tampered'", "DELETE FROM resolutions"]:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    # An administrator can disable SQL guards, but an incomplete rewrite is detected.
    with sqlite3.connect(db_path) as db:
        db.execute("DROP TRIGGER audit_no_update")
        db.execute("UPDATE audit SET note='tampered' WHERE action='resolve'")
    assert client.get(f"/api/runs/{run['id']}").status_code == 409
    assert client.get(f"/api/runs/{run['id']}/export").status_code == 409


def test_bundled_downloads_and_frontend_path_boundary(tmp_path, monkeypatch):
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "assets").mkdir()
    (frontend / "index.html").write_text("<h1>RinkCheck</h1>")
    (frontend / "assets" / "main.js").write_text("// production bundle")
    (tmp_path / "secret.txt").write_text("must not leak")
    monkeypatch.setenv("RINKCHECK_FRONTEND_DIR", str(frontend))
    with TestClient(create_app(tmp_path / "app.sqlite", seed=False)) as client:
        assert client.get("/").text == "<h1>RinkCheck</h1>"
        assert client.get("/review").text == "<h1>RinkCheck</h1>"
        assert client.get("/assets/main.js").status_code == 200
        assert client.get("/%2e%2e/secret.txt").status_code == 404
        assert client.get("/api/unknown").status_code == 404
        assert client.get("/api/template").status_code == 200
        assert client.get("/api/demo-files/opening/scorer").text == demo_pair("opening")[0]
        assert client.get("/api/demo-files/opening/not-a-source").status_code == 422
