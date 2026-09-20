"""Behavioral contracts for evidence preservation and deterministic reconciliation."""

import csv
import hashlib
import io
from pathlib import Path

import pytest

from rinkcheck.demo import demo_description, demo_pair
from rinkcheck.engine import analyze, candidate_evidence_hash, canonical_csv, standings
from rinkcheck.rules import COLUMNS, RULE_VERSION


def row(**changes):
    base = {"season": "2026-27", "game_id": "RC-1001", "game_date": "2026-09-18",
            "home_team": "Rivergate Comets", "away_team": "Northfield Foxes",
            "home_goals": "3", "away_goals": "2", "home_shots": "25", "away_shots": "24",
            "status": "final", "venue": "Rivergate Ice Hall", "updated_at": "2026-09-18T18:00:00Z"}
    return {**base, **changes}


def csv_text(rows, fields=COLUMNS):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def case_for(analysis, suffix):
    return next(c for c in analysis["cases"] if c["key"].endswith(suffix))


def test_opening_is_real_data_with_partitioned_findings_and_no_silent_losses():
    analysis = analyze(*demo_pair())
    assert analysis["rule_version"] == RULE_VERSION
    assert analysis["stats"] == {"input_rows": 48, "game_count": 24, "auto_matched": 16,
                                 "normalization_count": 5, "exact_duplicates": 1}
    assert len(analysis["cases"]) == 8
    automatic_keys = {g["key"] for g in analysis["canonical"]}
    case_keys = {c["key"] for c in analysis["cases"]}
    assert automatic_keys.isdisjoint(case_keys)
    assert automatic_keys | case_keys == {c["key"] for c in analysis["candidates"]}
    assert len(automatic_keys | case_keys) == 24
    retained = [cid for g in analysis["canonical"] for cid in g["provenance"]]
    retained += [cid for case in analysis["cases"] for cid in case["candidate_ids"]]
    assert sorted(retained) == sorted(c["id"] for c in analysis["candidates"])


def test_all_findings_retained_for_invalid_group():
    a = analyze(csv_text([row()]), csv_text([row(away_team="Rivergate Comets", home_shots="1")]))
    case = a["cases"][0]
    assert case["kind"] == "invalid"
    assert set(case["rule_ids"]) == {"FIELD_CONFLICT", "SCORE_EXCEEDS_SHOTS", "TEAM_COLLISION"}
    assert {"away_team", "home_shots"} <= set(case["fields"])
    assert case["severity"] == "critical"
    assert not a["candidates"][1]["valid"]


def test_same_names_and_date_with_different_ids_are_not_joined():
    a = analyze(csv_text([row(game_id="DOUBLE-1")]), csv_text([row(game_id="DOUBLE-2")]))
    assert not a["canonical"]
    assert len(a["cases"]) == 2
    assert all(c["rule_ids"] == ["MISSING_SOURCE"] for c in a["cases"])


def test_invalid_identities_never_coalesce_into_one_game():
    a = analyze(csv_text([row(game_id=""), row(game_id="")]), csv_text([row(game_id="")]))
    assert a["stats"]["game_count"] == 3
    assert len(a["cases"]) == 3
    assert all(len(c["candidate_ids"]) == 1 for c in a["cases"])


def test_explicit_normalization_preserves_raw_and_does_not_fuzzy_match():
    a = analyze(csv_text([row()]), csv_text([row(home_team="  Rivergate   C. ", status="FINAL")]))
    assert len(a["canonical"]) == 1
    assert a["canonical"][0]["home_team"] == "Rivergate Comets"
    assert a["candidates"][1]["raw"]["home_team"] == "  Rivergate   C. "
    assert {n["reason"] for n in a["normalizations"]} >= {"Explicit team alias", "Canonical status spelling"}
    typo = analyze(csv_text([row()]), csv_text([row(home_team="Rivergate Cometz")]))
    assert typo["cases"][0]["fields"] == ["home_team"]


@pytest.mark.parametrize("value", ["NaN", "true", "false", "3.0", "-1", "+2", "1e3", "3,000", "", "1000000000"])
def test_numeric_validation_quarantines_bad_final_values(value):
    a = analyze(csv_text([row()]), csv_text([row(home_goals=value)]))
    assert not a["candidates"][1]["valid"]
    assert "ROW_INVALID" in a["cases"][0]["rule_ids"]
    assert a["candidates"][1]["raw"]["home_goals"] == value
    assert not a["canonical"]


def test_scheduled_requires_all_or_none_stats_and_does_not_affect_standings():
    scheduled = row(status="scheduled", home_goals="", away_goals="", home_shots="", away_shots="")
    a = analyze(csv_text([scheduled]), csv_text([scheduled]))
    assert a["canonical"][0]["home_goals"] is None
    assert standings(a["canonical"]) == []
    partial = {**scheduled, "home_goals": "0"}
    b = analyze(csv_text([scheduled]), csv_text([partial]))
    assert "ROW_INVALID" in b["cases"][0]["rule_ids"]


@pytest.mark.parametrize("field,value", [
    ("game_date", "2026-02-30"), ("game_date", "20260918"),
    ("updated_at", "2026-09-18T18:00:00"), ("updated_at", "yesterday"),
    ("season", "2026-29"), ("game_id", "has space"), ("venue", ""), ("status", "unknown"),
])
def test_invalid_field_data_is_retained_not_ingestion_failure(field, value):
    a = analyze(csv_text([row()]), csv_text([row(**{field: value})]))
    candidate = a["candidates"][1]
    assert not candidate["valid"]
    assert candidate["raw"][field] == value
    assert any(error["field"] == field for error in candidate["errors"])


def test_timestamps_are_evidence_but_do_not_choose_a_winner():
    a = analyze(csv_text([row()]), csv_text([row(updated_at="2026-09-18T14:05:00-04:00")]))
    assert len(a["canonical"]) == 1
    assert a["canonical"][0]["updated_at"] == "2026-09-18T18:00:00Z"
    assert a["candidates"][1]["normalized"]["updated_at"] == "2026-09-18T18:05:00Z"


def test_duplicates_preserve_provenance_and_timestamp_difference_not_counted_exact():
    scorer = csv_text([row(), row(), row(updated_at="2026-09-18T19:00:00Z")])
    a = analyze(scorer, csv_text([row()]))
    assert a["stats"]["exact_duplicates"] == 1
    assert len(a["canonical"]) == 1
    assert len(a["canonical"][0]["provenance"]) == 4
    b = analyze(csv_text([row(), row(home_goals="4")]), csv_text([row()]))
    assert len(b["cases"][0]["candidate_ids"]) == 3
    assert "CONFLICTING_DUPLICATE" in b["cases"][0]["rule_ids"]


def test_invalid_raw_values_are_not_collapsed_to_the_same_null():
    a = analyze(csv_text([row(home_goals="NaN"), row(home_goals="oops")]), csv_text([row()]))
    assert "CONFLICTING_DUPLICATE" in a["cases"][0]["rule_ids"]
    assert a["stats"]["exact_duplicates"] == 0


def test_fingerprints_stable_on_reordering_and_sensitive_to_raw_content():
    left = [row(), row(game_id="RC-1002"), row(game_id="RC-1002")]
    right = [row(home_goals="4"), row(game_id="RC-1002", home_goals="4")]
    original = analyze(csv_text(left), csv_text(right))
    reordered = analyze(csv_text(list(reversed(left))), csv_text(list(reversed(right))))
    assert {c["key"]: c["fingerprint"] for c in original["cases"]} == {c["key"]: c["fingerprint"] for c in reordered["cases"]}
    original_candidate = next(c for c in original["candidates"] if c["raw"]["game_id"] == "RC-1001")
    reordered_candidate = next(c for c in reordered["candidates"] if c["raw"]["game_id"] == "RC-1001")
    assert original_candidate["id"] != reordered_candidate["id"]
    assert candidate_evidence_hash(original_candidate) == candidate_evidence_hash(reordered_candidate)
    right[0]["venue"] += " "
    raw_change = analyze(csv_text(left), csv_text(right))
    assert case_for(original, "RC-1001")["fingerprint"] != case_for(raw_change, "RC-1001")["fingerprint"]
    assert case_for(original, "RC-1002")["fingerprint"] == case_for(raw_change, "RC-1002")["fingerprint"]


def test_fingerprints_include_duplicate_multiplicity_and_extra_named_columns():
    fields = (*COLUMNS, "operator_note")
    left = row(operator_note="checked")
    right = row(home_goals="4", operator_note="checked")
    a = analyze(csv_text([left], fields), csv_text([right], fields))
    b = analyze(csv_text([left, left], fields), csv_text([right], fields))
    c = analyze(csv_text([left], fields), csv_text([{**right, "operator_note": "changed"}], fields))
    assert len({x["cases"][0]["fingerprint"] for x in (a, b, c)}) == 3
    assert "operator_note" not in a["candidates"][0]["normalized"]


def test_followup_changes_exactly_one_issue_and_sources_have_real_checksums():
    opening_pair, followup_pair = demo_pair(), demo_pair("followup")
    opening, followup = analyze(*opening_pair), analyze(*followup_pair)
    previous = {c["key"]: c["fingerprint"] for c in opening["cases"]}
    unchanged = [c for c in followup["cases"] if previous[c["key"]] == c["fingerprint"]]
    assert len(unchanged) == 7
    for source, content in zip(opening["sources"], opening_pair):
        assert source["sha256"] == hashlib.sha256(content.encode()).hexdigest()


@pytest.mark.parametrize("bad", ["", " \n", "season,game_id\n2026-27,A\n", ",season\na,b\n",
                                  ",".join(COLUMNS) + "\n", "\ud800", "\x00"])
def test_malformed_files_reject_atomically(bad):
    with pytest.raises(ValueError):
        analyze(bad, csv_text([row()]))


def test_duplicate_headers_extra_cells_and_unclosed_quotes_reject():
    valid = csv_text([row()])
    for bad in [valid.replace("season,", "season,season,", 1),
                valid.rstrip("\n") + ",extra\n", ",".join(COLUMNS) + '\n"unterminated']:
        with pytest.raises(ValueError):
            analyze(bad, valid)


def test_truncated_row_retained_as_invalid_and_limits_reject():
    valid = csv_text([row()])
    truncated = ",".join(COLUMNS) + "\n2026-27,SHORT\n"
    a = analyze(truncated, valid)
    assert not a["candidates"][0]["valid"]
    assert a["candidates"][0]["raw"]["updated_at"] is None
    for bad in [valid + " " * 1_048_576, csv_text([row()] * 5001)]:
        with pytest.raises(ValueError):
            analyze(bad, valid)


def test_utf8_bom_and_quoted_comma_are_accepted_with_exact_source_hash():
    content = "\ufeff" + csv_text([row(venue="Ice Hall, East")])
    a = analyze(content, content)
    assert a["canonical"][0]["venue"] == "Ice Hall, East"
    assert a["sources"][0]["sha256"] == hashlib.sha256(content.encode()).hexdigest()


def test_standings_invariants_points_ties_and_stable_sort():
    rows = [row(), row(game_id="RC-1002", home_goals="2", away_goals="2"),
            row(game_id="RC-1003", home_goals="0", away_goals="1")]
    games = analyze(csv_text(rows), csv_text(rows))["canonical"]
    table = standings(games)
    assert all(t["played"] == 3 and t["wins"] == 1 and t["losses"] == 1 and t["ties"] == 1 and t["points"] == 3 for t in table)
    assert sum(t["goals_for"] for t in table) == sum(t["goals_against"] for t in table)
    assert sum(t["played"] for t in table) == 2 * len(games)
    assert table[0]["team"] == "Northfield Foxes"  # alphabetical after all numeric ties
    assert standings(list(reversed(games))) == table
    with pytest.raises(ValueError, match="unique"):
        standings(games + [games[0]])


@pytest.mark.parametrize("payload", ["=HYPERLINK(\"https://example.invalid\")", "+1+1", "-1+1", "@SUM(A1)", " \t=1+1", "\ttext"])
def test_csv_formula_injection_neutralized(payload):
    game = analyze(csv_text([row()]), csv_text([row()]))["canonical"][0]
    game["venue"] = payload
    exported = list(csv.DictReader(io.StringIO(canonical_csv([game]))))
    assert exported[0]["venue"] == "'" + payload


def test_canonical_export_stable_and_omits_extra_fields():
    games = analyze(*demo_pair("clean"))["canonical"]
    assert canonical_csv(games) == canonical_csv(list(reversed(games)))
    parsed = list(csv.DictReader(io.StringIO(canonical_csv(games))))
    assert list(parsed[0]) == list(COLUMNS)
    assert parsed[-1]["home_goals"] == ""
    assert "provenance" not in parsed[0]


def test_fixture_files_reproduce_exact_demo_bytes():
    fixture_dir = Path(__file__).resolve().parents[2] / "fixtures"
    for variant in ("opening", "followup", "clean"):
        assert "synthetic" in demo_description(variant).lower()
        for source, content in zip(("scorer", "league"), demo_pair(variant)):
            assert (fixture_dir / f"{variant}_{source}.csv").read_bytes() == content.encode()
    with pytest.raises(ValueError):
        demo_pair("made-up")
