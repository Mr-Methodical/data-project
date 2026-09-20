# Explain RinkCheck from first principles

RinkCheck is a hockey data-reconciliation workbench. A scorer's game export and a league export may disagree. The application keeps both originals, normalizes only documented formatting differences, puts substantive discrepancies in a review queue, and produces an evidence-backed canonical export once every issue has a disposition.

The hockey setting gives the project a concrete motivation: a wrong score changes standings, while a missing result can make a plausible-looking dashboard incomplete. The connection to data-integrity work is reconciliation, explicit rules, traceable decisions, and preventing unresolved data from silently entering downstream reporting. The fixtures are synthetic. This project has no league affiliation, operational users, or measured business savings.

## A five-minute walkthrough

1. **Overview:** Explain the two inputs, the synthetic-data label, and the denominator of publishable coverage. It is a coverage ratio, not model confidence or a statement that the source data is true.
2. **Review queue:** Open a disagreement. Show raw evidence alongside normalized candidates. Describe the exact rule that caused review and why the application does not choose the newer row automatically.
3. **Decision preview:** Select a valid candidate and inspect changes to the published subset and standings before committing. Enter a specific rationale, not "looks good."
4. **Lineage and audit:** Trace the selected record to its input row. Show the operator label, decision, reason, and hash-linked history. Explain why a self-reported label is not authentication.
5. **Follow-up release:** Load the next synthetic release. Replay only decisions whose entire evidence fingerprint and rules version match. Point out the changed case that still needs human review.
6. **Export:** Show that open cases block export. After reviewing them, inspect the manifest, canonical CSV, audit JSON, and two raw files. If you excluded a game, show the explicit incomplete-coverage status.

Practice this with the app open. Do not memorize a script that hides a behavior you cannot explain.

## Questions you should be able to answer

### What does data integrity mean here?

It means the canonical output follows stated validation and reconciliation rules, its origin is recoverable, and changes are deliberate and recorded. It does not mean the system knows what happened on the ice. Two sources can agree on the same incorrect score. The project detects specified inconsistencies and preserves evidence for review; external truth requires an authoritative record or a human investigation.

### Why join on `(season, game_id)` instead of team names and date?

The identity must be stable and shared between feeds. Two games can have the same teams or similar names. Joining on fuzzy names risks combining different games and hiding a missing record. This prototype assumes both feeds share game IDs. A real adapter would need an explicit, reviewed crosswalk if they do not. Malformed identity is quarantined rather than joined with other malformed rows.

### Which changes can safely happen automatically?

Documented Unicode and whitespace normalization and explicit team aliases are deterministic. Normalization is logged so the reviewer can see both representations. Scores, shots, status, dates, and substantive field differences require review. There is no fuzzy join, silent imputation, score averaging, or automatic "latest timestamp wins" rule.

### Why is review part of the architecture?

Validation can prove that a value breaks a rule without proving its replacement. A goals-greater-than-shots record is invalid, but that does not establish the correct number of shots. Review selects a complete valid candidate or explicitly excludes the game. It never manufactures a new composite record from whichever fields happen to look best.

### What stops an invalid or stale decision from being applied?

The server owns validation. It checks that the chosen candidate belongs to the case and is valid. A submitted expected version must match an open case. SQLite's write transaction coordinates checking, persisting the resolution, and adding its audit event. A second submission against an already resolved case is rejected with HTTP 409. Disabling a UI button alone would not provide this guarantee.

### What makes decision replay safer than matching a game ID?

The game ID identifies a subject; it does not identify unchanged evidence. Replay compares a fingerprint of the entire candidate evidence plus the rule version, and a selected candidate must have the same evidence hash. A changed score, raw field, or policy prevents reuse. CSV row order alone does not invalidate equivalent evidence. A replay audit record links to the prior human decision, and replayed decisions do not become new original human decisions for further propagation.

### Why SQLite, and where does it stop being suitable?

For one local worker and bounded CSV files, SQLite removes deployment dependencies and provides real transactions and persistent history. The server uses a disk-backed file, not an in-memory demo store. This design is intentionally not a distributed database. Multiple replicas and network-mounted SQLite would need a different persistence plan. PostgreSQL is a reasonable future option, but moving to it also requires testing the concurrency and evidence invariants.

### Is the audit trail tamper-proof?

No. Entries are hash-linked and application-level updates/deletes are blocked with database triggers. This detects accidental changes and partial alteration. An administrator with full database control could remove protections and reconstruct the chain. Stronger evidence would require independent signed checkpoints or an external append-only/immutable store plus authenticated actors. A SHA-256 checksum alone is not a digital signature.

### What if every issue is "resolved" by excluding all the games?

Review completion and data completeness are different. Exclusions remain in the manifest with reasons. Coverage still uses all unique game groups as its denominator. The exported set may be valid but incomplete, and the product says so. Treating "no open issues" as "all source records successfully published" would be a misleading metric.

### Why preserve raw input if normalized data is easier to use?

Normalization can erase evidence about source quality and parsing decisions. Immutable raw files and source checksums make later reproduction and review possible. Canonical CSV escapes spreadsheet-formula-like text; raw CSVs preserve original content and must be handled as untrusted evidence.

### Why not use an LLM to fix the discrepancies?

The runtime uses explicit deterministic rules. This makes behavior reproducible, inspectable, and testable. AI-assisted development helped create the application; the product does not require an AI API or send source records to a model. A possible future assistant could explain a flagged rule or draft a rationale, but it should not silently choose the official result.

### What does Azure contribute?

The optional CLI validates a reviewed export and archives it to Blob Storage with a content-hash object name and checksum metadata, using Entra credentials. It does not deploy the app, authenticate reviewers, or turn an ordinary blob into immutable storage. The integration can be verified locally with `--dry-run`; an actual cloud upload is a separate opt-in step.

## Read the code in this order

| File | Find and explain |
| --- | --- |
| `fixtures/opening_scorer.csv` and `fixtures/opening_league.csv` | One formatting difference, one substantive conflict, and one missing record. |
| `backend/rinkcheck/rules.py` | The policy behind the selected case. |
| `backend/rinkcheck/engine.py` | Parsing, grouping, validation, fingerprints, canonical CSV, and standings. |
| `backend/rinkcheck/store.py` | Evidence persistence, resolution transaction, version rejection, replay lookup, and export gate. |
| `backend/rinkcheck/api.py` | Request validation, size limits, error responses, and serving the built frontend. |
| `frontend/src/` | How the review interface retrieves evidence, previews a decision, and refreshes server state. |
| `backend/tests/` | Tests that would catch an incorrect merge, stale review, or export with unresolved issues. |
| `scripts/archive_to_azure.py` | Exact ZIP membership, checksum verification, destination validation, and no-overwrite behavior. |

## Prove you understand it by changing it

These are useful learning tasks, not features already claimed by the project:

1. Add one explicit team alias and a regression test proving two unrelated teams are still separate.
2. Change a fixture's score after a prior resolution and demonstrate why replay skips it.
3. Submit two simultaneous decisions for one case and explain the observed 409.
4. Explain what a standings tie-breaker change would require in rules versioning, tests, and the UI policy text.
5. Download a bundle, alter one member without updating its checksum, and show the archive verifier rejecting it.

## Describe AI assistance candidly

This project was built with substantial AI coding assistance. Review, run, test, and extend it before claiming independent ownership of implementation details. A useful interview account separates the product goal, decisions you understand and can defend, code you personally changed, and where the assistant generated implementation. Keep a short personal change log of the fixes and trade-offs you worked through.

An accurate project description once you can demonstrate the behavior is:

> Developed an AI-assisted hockey data-reconciliation workbench with a React review UI and FastAPI/SQLite backend; validated conflicting CSV feeds, preserved source lineage, and gated canonical exports on reviewed decisions.

Add the Azure archive only after you have exercised it; say "optional Blob archive adapter" if it remains unconnected. Do not claim production deployment, real users, measured savings, or expertise with every dependency from having generated the repository.
