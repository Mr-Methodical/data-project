#!/usr/bin/env python3
"""Verify a reviewed RinkCheck export, then optionally archive it in Azure Blob.

Only `main` uploads. Importing this module or using --dry-run requires no Azure
SDK, credentials, or cloud connection. This is an archive adapter, not hosting.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any


MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
EXPECTED_MEMBERS = frozenset(
    {"canonical.csv", "manifest.json", "audit.json", "raw_scorer.csv", "raw_league.csv"}
)
CHECKSUM_MEMBERS = EXPECTED_MEMBERS - {"manifest.json"}
CANONICAL_COLUMNS = (
    "season", "game_id", "game_date", "home_team", "away_team", "home_goals",
    "away_goals", "home_shots", "away_shots", "status", "venue", "updated_at",
)
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class ExportValidationError(ValueError):
    """An archive does not match the export's documented contract."""


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ExportValidationError("API redirects are not accepted; use the final API URL.")


def export_url(api_url: str, run_id: str) -> str:
    """Build a fixed endpoint; the CLI is not an arbitrary download proxy."""
    if not SAFE_ID.fullmatch(run_id):
        raise ExportValidationError("Run ID must contain only letters, digits, '.', '_' or '-'.")
    parsed = urllib.parse.urlsplit(api_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ExportValidationError("API URL must be an HTTP(S) origin without credentials, path or query.")
    # Local development may use HTTP. A remote endpoint must provide TLS.
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ExportValidationError("Plain HTTP is allowed only for loopback addresses; use HTTPS remotely.")
    try:
        parsed.port
    except ValueError as exc:
        raise ExportValidationError("Invalid API port.") from exc
    return api_url.rstrip("/") + "/api/runs/" + urllib.parse.quote(run_id, safe="") + "/export"


def fetch_export(api_url: str, run_id: str) -> bytes:
    request = urllib.request.Request(export_url(api_url, run_id), headers={"Accept": "application/zip"})
    opener = urllib.request.build_opener(NoRedirects())
    try:
        with opener.open(request, timeout=30) as response:
            if response.headers.get_content_type() != "application/zip":
                raise ExportValidationError("The API did not return a ZIP export.")
            declared = response.headers.get("Content-Length")
            if declared and (not declared.isdecimal() or int(declared) > MAX_EXPORT_BYTES):
                raise ExportValidationError("Export exceeds the 16 MiB compressed size limit.")
            payload = response.read(MAX_EXPORT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise ExportValidationError("Run is not exportable; resolve every open case first.") from exc
        raise ExportValidationError(f"Export API returned HTTP {exc.code}.") from exc
    if len(payload) > MAX_EXPORT_BYTES:
        raise ExportValidationError("Export exceeds the 16 MiB compressed size limit.")
    return payload


def _strict_json(payload: bytes) -> Any:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ExportValidationError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ExportValidationError(f"Invalid JSON constant: {value}")

    try:
        return json.loads(payload, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportValidationError("Export contains invalid JSON.") from exc


def verify_export(payload: bytes, expected_run_id: str) -> dict[str, Any]:
    """Validate archive shape and SHA-256 of every payload before any upload.

    This establishes internal consistency, not authenticity of the API server.
    The manifest cannot contain its own checksum; the whole ZIP gets a separate
    SHA-256 used in its object key and metadata. No archive member is extracted.
    """
    if len(payload) > MAX_EXPORT_BYTES:
        raise ExportValidationError("Export exceeds the 16 MiB compressed size limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(EXPECTED_MEMBERS) or set(names) != EXPECTED_MEMBERS:
                raise ExportValidationError("Export must contain exactly the five expected files, without duplicates.")
            if any(entry.flag_bits & 1 or entry.is_dir() for entry in entries):
                raise ExportValidationError("Encrypted or directory entries are not supported.")
            if sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES:
                raise ExportValidationError("Export exceeds the 32 MiB uncompressed size limit.")
            members = {}
            for entry in entries:
                # Also bound reads independently of the untrusted central directory.
                with archive.open(entry) as stream:
                    content = stream.read(MAX_UNCOMPRESSED_BYTES + 1)
                if len(content) > MAX_UNCOMPRESSED_BYTES or len(content) != entry.file_size:
                    raise ExportValidationError("Archive member size is invalid.")
                members[entry.filename] = content
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
        raise ExportValidationError("Export is not a supported, intact ZIP archive.") from exc

    manifest = _strict_json(members["manifest.json"])
    if not isinstance(manifest, dict) or manifest.get("run_id") != expected_run_id:
        raise ExportValidationError("Manifest run ID does not match the requested run.")
    version = manifest.get("rule_version")
    if not isinstance(version, str) or not SAFE_ID.fullmatch(version):
        raise ExportValidationError("Manifest rule version is missing or invalid.")
    if (
        type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
        or type(manifest.get("open_cases")) is not int
        or manifest["open_cases"] != 0
    ):
        raise ExportValidationError("Manifest must use schema 1 and have no unresolved cases.")
    for field in ("total_groups", "published_count", "excluded_count"):
        if type(manifest.get(field)) is not int or manifest[field] < 0:
            raise ExportValidationError(f"Manifest {field} must be a nonnegative integer.")
    if manifest["published_count"] + manifest["excluded_count"] != manifest["total_groups"]:
        raise ExportValidationError("Published and excluded counts must account for every group.")
    excluded_keys = manifest.get("excluded_keys")
    if (
        not isinstance(excluded_keys, list)
        or any(not isinstance(key, str) for key in excluded_keys)
        or len(set(excluded_keys)) != len(excluded_keys)
        or len(excluded_keys) != manifest["excluded_count"]
    ):
        raise ExportValidationError("Excluded keys do not match the manifest counts.")
    expected_status = "partial_with_exclusions" if excluded_keys else "complete"
    if manifest.get("completeness_status") != expected_status:
        raise ExportValidationError("Completeness status does not reflect exclusions.")
    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict) or set(checksums) != CHECKSUM_MEMBERS:
        raise ExportValidationError("Manifest must checksum every payload file exactly once.")
    for name in sorted(CHECKSUM_MEMBERS):
        actual = hashlib.sha256(members[name]).hexdigest()
        if checksums[name] != actual:
            raise ExportValidationError(f"SHA-256 mismatch for {name}.")
    try:
        rows = csv.DictReader(io.StringIO(members["canonical.csv"].decode("utf-8"), newline=""), strict=True)
        if tuple(rows.fieldnames or ()) != CANONICAL_COLUMNS:
            raise ExportValidationError("Canonical CSV has unexpected columns.")
        keys = []
        for row in rows:
            if None in row or any(value is None for value in row.values()):
                raise ExportValidationError("Canonical CSV has a malformed row.")
            keys.append(f"{row['season']}/{row['game_id']}")
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ExportValidationError("Canonical CSV is not valid UTF-8 CSV.") from exc
    if len(keys) != manifest["published_count"] or len(set(keys)) != len(keys):
        raise ExportValidationError("Canonical CSV rows do not match published count or contain duplicate game keys.")
    if set(keys) & set(excluded_keys):
        raise ExportValidationError("An excluded key appears in the canonical CSV.")
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != set(keys):
        raise ExportValidationError("Manifest provenance does not account for every published key.")
    if any(
        not isinstance(value, list)
        or not value
        or any(not isinstance(candidate, str) or not candidate for candidate in value)
        for value in provenance.values()
    ):
        raise ExportValidationError("Every published key must retain source candidate provenance.")
    if not isinstance(_strict_json(members["audit.json"]), list):
        raise ExportValidationError("Audit payload must be an array.")
    return manifest


def validate_destination(account_url: str, container: str) -> None:
    # A bearer-token client must never receive a user-chosen non-Azure endpoint.
    if not re.fullmatch(r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net/?", account_url):
        raise ExportValidationError("Account URL must be an HTTPS public-Azure Blob Storage endpoint.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", container) or "--" in container:
        raise ExportValidationError("Container must be a valid lowercase Azure container name.")


def archive_verified_export(payload: bytes, manifest: dict[str, Any], account_url: str, container: str) -> dict:
    """Upload by content hash without overwriting; verify bytes on a repeat call."""
    validate_destination(account_url, container)
    try:
        from azure.core.exceptions import ResourceExistsError
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient, ContentSettings
    except ImportError as exc:
        raise ExportValidationError("Install optional dependencies: pip install -r backend/requirements-azure.txt") from exc

    digest = hashlib.sha256(payload).hexdigest()
    blob_name = f"exports/{manifest['run_id']}/{digest}.zip"
    metadata = {"sha256": digest, "rule_version": manifest["rule_version"], "run_id": manifest["run_id"]}
    with DefaultAzureCredential() as credential:
        with BlobClient(
            account_url=account_url,
            container_name=container,
            blob_name=blob_name,
            credential=credential,
            connection_timeout=10,
            read_timeout=30,
        ) as blob:
            status = "uploaded"
            try:
                blob.upload_blob(
                    payload,
                    overwrite=False,
                    metadata=metadata,
                    content_settings=ContentSettings(content_type="application/zip"),
                    validate_content=True,
                    timeout=60,
                )
            except ResourceExistsError:
                properties = blob.get_blob_properties(timeout=30)
                if properties.size != len(payload):
                    raise ExportValidationError("Existing blob has unexpected size; nothing was overwritten.")
                stored = blob.download_blob(offset=0, length=len(payload), timeout=30).readall()
                if hashlib.sha256(stored).hexdigest() != digest:
                    raise ExportValidationError("Existing blob failed checksum verification; nothing was overwritten.")
                status = "already_present_verified"
            return {"status": status, "url": blob.url, "sha256": digest, "bytes": len(payload)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="Run ID from /api/runs")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000", help="RinkCheck HTTP(S) origin")
    parser.add_argument("--account-url", help="https://ACCOUNT.blob.core.windows.net")
    parser.add_argument("--container", default="rinkcheck-exports")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and verify locally; never authenticate or upload")
    args = parser.parse_args(argv)
    if not args.dry_run and not args.account_url:
        parser.error("--account-url is required unless --dry-run is set")
    try:
        if args.account_url:
            validate_destination(args.account_url, args.container)
        payload = fetch_export(args.api_url, args.run_id)
        manifest = verify_export(payload, args.run_id)
        if args.dry_run:
            digest = hashlib.sha256(payload).hexdigest()
            result = {"status": "verified_only", "run_id": args.run_id, "rule_version": manifest["rule_version"],
                      "blob_name": f"exports/{args.run_id}/{digest}.zip", "sha256": digest, "bytes": len(payload)}
        else:
            result = archive_verified_export(payload, manifest, args.account_url, args.container)
        print(json.dumps(result, indent=2))
        return 0
    except (ExportValidationError, urllib.error.URLError, TimeoutError) as exc:
        print(f"Archive failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
