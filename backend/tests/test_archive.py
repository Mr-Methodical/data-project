"""Archive boundary tests using real reviewed exports, without Azure access."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import urllib.error
import zipfile

import pytest

from rinkcheck.demo import demo_pair
from rinkcheck.store import Store


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "archive_to_azure.py"
SPEC = importlib.util.spec_from_file_location("rinkcheck_archive_under_test", SCRIPT)
archive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(archive)


@pytest.fixture
def reviewed_export(tmp_path):
    store = Store(tmp_path / "archive.db")
    scorer, league = demo_pair("clean")
    run = store.create_run("Clean review fixture", scorer, league, is_demo=True)
    payload, _ = store.export(run["id"])
    return store, run, payload


def unpack(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        return {name: bundle.read(name) for name in bundle.namelist()}


def repack(members):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return output.getvalue()


def change_manifest(payload, transform):
    members = unpack(payload)
    manifest = json.loads(members["manifest.json"])
    transform(manifest)
    members["manifest.json"] = json.dumps(manifest).encode()
    return repack(members)


def test_clean_export_from_application_is_accepted(reviewed_export):
    _, run, payload = reviewed_export
    manifest = archive.verify_export(payload, run["id"])
    assert manifest["completeness_status"] == "complete"
    assert manifest["published_count"] == run["published_games"]


def test_completed_export_with_quarantined_games_is_accepted(tmp_path):
    store = Store(tmp_path / "quarantine.db")
    scorer, league = demo_pair("opening")
    run = store.create_run("Quarantine fixture", scorer, league, is_demo=True)
    assert run["open_cases"] > 0
    for case in run["cases"]:
        store.resolve(run["id"], case["id"], "exclude", None, 0,
                      "Quarantined pending a corrected original feed.", "Archive regression")
    payload, _ = store.export(run["id"])
    manifest = archive.verify_export(payload, run["id"])
    assert manifest["completeness_status"] == "partial_with_exclusions"
    assert len(manifest["excluded_keys"]) == len(run["cases"])
    assert manifest["published_count"] + manifest["excluded_count"] == run["game_count"]


@pytest.mark.parametrize("member", sorted(archive.CHECKSUM_MEMBERS))
def test_changed_payload_is_rejected_before_archival(reviewed_export, member):
    _, run, payload = reviewed_export
    members = unpack(payload)
    members[member] += b"\nUnreviewed change"
    with pytest.raises(archive.ExportValidationError, match="SHA-256 mismatch"):
        archive.verify_export(repack(members), run["id"])


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate"])
def test_unexpected_archive_members_are_rejected(reviewed_export, change):
    _, run, payload = reviewed_export
    members = unpack(payload)
    if change == "missing":
        del members["raw_league.csv"]
        altered = repack(members)
    elif change == "extra":
        members["../unexpected.txt"] = b"Never extracted"
        altered = repack(members)
    else:
        output = io.BytesIO(repack(members))
        with zipfile.ZipFile(output, "a") as bundle, pytest.warns(UserWarning, match="Duplicate name"):
            bundle.writestr("canonical.csv", members["canonical.csv"])
        altered = output.getvalue()
    with pytest.raises(archive.ExportValidationError, match="exactly the five"):
        archive.verify_export(altered, run["id"])


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_every_payload_requires_exactly_one_checksum(reviewed_export, change):
    _, run, payload = reviewed_export

    def alter(manifest):
        if change == "missing":
            del manifest["checksums"]["audit.json"]
        else:
            manifest["checksums"]["unlisted.csv"] = "0" * 64

    with pytest.raises(archive.ExportValidationError, match="checksum every payload"):
        archive.verify_export(change_manifest(payload, alter), run["id"])


@pytest.mark.parametrize("updates", [
    {"open_cases": 1},
    {"open_cases": False},
    {"schema_version": 2},
    {"schema_version": True},
    {"schema_version": 1.0},
    {"published_count": -1},
    {"published_count": True},
    {"excluded_count": 1},
    {"excluded_keys": ["undeclared/game"]},
    {"completeness_status": "partial_with_exclusions"},
    {"rule_version": "../arbitrary-path"},
])
def test_unresolved_or_inconsistent_manifest_is_rejected(reviewed_export, updates):
    _, run, payload = reviewed_export
    altered = change_manifest(payload, lambda manifest: manifest.update(updates))
    with pytest.raises(archive.ExportValidationError):
        archive.verify_export(altered, run["id"])


def test_balanced_but_false_published_count_is_rejected(reviewed_export):
    _, run, payload = reviewed_export
    assert run["published_games"] > 0
    altered = change_manifest(payload, lambda manifest: manifest.update(
        published_count=0, excluded_count=0, total_groups=0,
    ))
    with pytest.raises(archive.ExportValidationError):
        archive.verify_export(altered, run["id"])


def test_wrong_run_and_non_zip_payload_are_rejected(reviewed_export):
    _, run, payload = reviewed_export
    with pytest.raises(archive.ExportValidationError, match="run ID"):
        archive.verify_export(payload, "another-run")
    with pytest.raises(archive.ExportValidationError, match="ZIP"):
        archive.verify_export(b"Not an export", run["id"])


def test_archive_compressed_and_expanded_size_limits_are_enforced(reviewed_export, monkeypatch):
    _, run, payload = reviewed_export
    with monkeypatch.context() as context:
        context.setattr(archive, "MAX_EXPORT_BYTES", len(payload) - 1)
        with pytest.raises(archive.ExportValidationError, match="compressed size"):
            archive.verify_export(payload, run["id"])
    expanded_size = sum(map(len, unpack(payload).values()))
    with monkeypatch.context() as context:
        context.setattr(archive, "MAX_UNCOMPRESSED_BYTES", expanded_size - 1)
        with pytest.raises(archive.ExportValidationError, match="uncompressed size"):
            archive.verify_export(payload, run["id"])


def test_duplicate_json_keys_and_invalid_audit_type_are_rejected(reviewed_export):
    _, run, payload = reviewed_export
    members = unpack(payload)
    members["manifest.json"] = b'{"run_id":"one","run_id":"two"}'
    with pytest.raises(archive.ExportValidationError, match="Duplicate JSON key"):
        archive.verify_export(repack(members), run["id"])
    members = unpack(payload)
    members["audit.json"] = b"{}"
    manifest = json.loads(members["manifest.json"])
    manifest["checksums"]["audit.json"] = hashlib.sha256(members["audit.json"]).hexdigest()
    members["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(archive.ExportValidationError, match="Audit payload"):
        archive.verify_export(repack(members), run["id"])


@pytest.mark.parametrize("origin", [
    "file:///tmp/review", "http://remote.example", "https://user:password@example.com",
    "https://example.com/subpath", "https://example.com?token=x",
    "https://example.com#fragment", "https://example.com:invalid",
])
def test_invalid_api_origins_are_rejected_without_network(origin):
    with pytest.raises(archive.ExportValidationError):
        archive.export_url(origin, "run-1")


def test_fixed_export_endpoint_and_run_id_validation():
    assert archive.export_url("http://127.0.0.1:8000/", "run-1") == \
        "http://127.0.0.1:8000/api/runs/run-1/export"
    with pytest.raises(archive.ExportValidationError):
        archive.export_url("http://127.0.0.1:8000", "../another/run")


def test_pending_api_export_is_rejected(monkeypatch):
    class PendingAPI:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 409, "Review required", {}, None)

    monkeypatch.setattr(archive.urllib.request, "build_opener", lambda *args: PendingAPI())
    with pytest.raises(archive.ExportValidationError, match="resolve every open case"):
        archive.fetch_export("http://127.0.0.1:8000", "run-1")


def test_dry_run_verifies_real_export_without_invoking_azure(reviewed_export, monkeypatch, capsys):
    _, run, payload = reviewed_export
    monkeypatch.setattr(archive, "fetch_export", lambda *_: payload)

    def unexpected_upload(*_):
        pytest.fail("Dry run must never authenticate or upload")

    monkeypatch.setattr(archive, "archive_verified_export", unexpected_upload)
    assert archive.main(["--run-id", run["id"], "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "verified_only"
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["bytes"] == len(payload)


@pytest.mark.parametrize("arguments", [
    [], ["--run-id", "run-1"], ["--run-id", "run-1", "--dry-run", "--unknown-option"],
])
def test_unsupported_cli_arguments_fail_before_network(arguments, monkeypatch):
    monkeypatch.setattr(archive, "fetch_export", lambda *_: pytest.fail("Must validate arguments before fetch"))
    with pytest.raises(SystemExit) as exc:
        archive.main(arguments)
    assert exc.value.code == 2
