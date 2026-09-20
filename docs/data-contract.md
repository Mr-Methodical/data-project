# Data contract and review semantics

RinkCheck compares two snapshots of the **same set of games**: an official-scorer export and a league export. Source names identify where evidence came from; neither source automatically wins a disagreement. The CSV adapter is deliberately explicit rather than a guessed schema mapper.

Download the [template](../fixtures/template.csv) or use the [bundled releases](../fixtures/README.md).

## Required columns

| Field | Meaning and validation |
| --- | --- |
| `season` | Consecutive `YYYY-YY`, for example `2026-27` |
| `game_id` | Stable shared identifier, 1–64 letters/digits/dots/underscores/hyphens, starting with a letter or digit |
| `game_date` | A real date in `YYYY-MM-DD` form |
| `home_team`, `away_team` | Nonempty, different team names after explicit normalization |
| `home_goals`, `away_goals` | Nonnegative integers; final games require values |
| `home_shots`, `away_shots` | Nonnegative integers, each at least the corresponding goals |
| `status` | `final` or `scheduled`, case-normalized |
| `venue` | Nonempty venue name |
| `updated_at` | ISO 8601 timestamp with seconds and an explicit timezone, for example `2026-09-20T18:30:00Z` |

For scheduled games, provide all four statistics or leave all four blank. Missing values are not filled with zero. Decimal numbers, negatives, signed numbers, NaN, infinity, and excessively long numeric strings do not become valid integers. The contract supports at most nine decimal digits per statistic; it does not infer league-specific realistic maxima.

The identity key is **`(season, game_id)`**. A common team name or matching date is insufficient to join records. Invalid identities are isolated as separate review groups instead of being merged into one missing-ID bucket.

## File boundary

- UTF-8 text, at most **1 MiB per source** and **5,000 data rows per source**.
- The API limits the whole JSON request to **3 MiB**, including JSON encoding overhead.
- Every required header must be present exactly once. Extra named columns are retained as evidence but are not canonical fields.
- Empty files, duplicate or blank headers, extra cells, NUL bytes, and malformed CSV quoting fail the entire import.
- A row with missing cells or invalid values stays visible as an invalid candidate. A reviewer can choose another valid candidate or quarantine the game; the app does not silently repair it.
- Blank physical lines are CSV separators. They are not game records.

These limits intentionally bound a local review session. They are not streaming ingestion or a claim of unrestricted file support.

## Safe normalization

Original cells remain in `raw`; normalized values and the reason for every change are stored separately. Transformations include Unicode NFKC, whitespace normalization, explicit team aliases, integer spelling, status spelling, and equivalent timestamps expressed in UTC.

The [versioned rule catalog](../backend/rinkcheck/rules.py) contains the accepted aliases and comparison fields. Unknown team names are preserved, not guessed. The ingestion timestamp does not decide which source is correct. A new normalization policy should change the rule version and be accompanied by tests.

## How a game becomes publishable

1. Both sources supply valid records with matching substantive values: publish automatically, retaining provenance.
2. The sources conflict, a source is missing, a candidate is invalid, or duplicates disagree: open a case with all evidence.
3. The reviewer chooses **one valid, case-local candidate**, inspects the preview, and submits a rationale. The chosen complete record becomes canonical.
4. Alternatively, the reviewer quarantines the entire game. It remains explicitly excluded from published data and reduces publishable coverage.

There is no field-by-field blending, averaging, fuzzy entity merging, or hidden preferred-source policy. Exact raw duplicates can be counted separately; normalized-equivalent records may agree, but all candidate provenance is retained.

## Decision reuse

A case fingerprint includes the rule version and the complete sorted evidence. It is independent of input row order but sensitive to changed raw values, timestamps, extra columns, and source identity. Selection is rebound by evidence identity, not by the old row number.

Reuse is an explicit operator action. Replayed events link to the prior human-reviewed decision; replay does not manufacture a new original approval. A matching game key alone never authorizes reuse.

## Export bundle

Export is blocked until every case has a decision. The ZIP contains:

| File | Content |
| --- | --- |
| `canonical.csv` | Stable canonical records with spreadsheet-formula escaping |
| `manifest.json` | Rule version, SHA-256 hashes, provenance, counts, exclusions, and completeness |
| `audit.json` | Review and replay events with linked hashes |
| `raw_scorer.csv`, `raw_league.csv` | Original uploaded text, unchanged |

An export with quarantined games is explicitly partial. Its completeness is not restored by marking the review queue finished. Hashes establish consistency of the bundled bytes; they do not establish the factual truth of a score or an authenticated reviewer's identity.

Raw evidence is intentionally not spreadsheet-sanitized. Treat it as untrusted input when opening it in a spreadsheet application. The original bytes remain available for investigation.
