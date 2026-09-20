# Architecture and tradeoffs

## Keep reconciliation independent of the interface

`engine.analyze(scorer_csv, league_csv)` is a deterministic function. It performs no network or database access. Its result contains source hashes, candidates, normalization explanations, automatically matched canonical games, and review cases. This separation makes malformed data, grouping, and rule changes testable without a browser.

The React client presents evidence and requests previews. The server recomputes canonical results and standings from stored evidence and accepted decisions. A browser cannot submit an arbitrary canonical row or override a candidate's validity.

## Preserve evidence; append decisions

SQLite stores an immutable run, both source texts, the original analysis, and its hash. Accepted decisions occupy a separate table. The audit stream records ingestion, human review, and replay. SQL triggers reject updates or deletion of evidence, accepted decisions, and audit events.

Each event hashes its canonical JSON and the previous event hash. Integrity checks compare raw source hashes, analysis hashes, and the audit chain before important reads or mutations. This detects inconsistency or partial tampering. It is not a digital signature: a sufficiently privileged database administrator could replace the database or rewrite all hashes. An external trusted anchor would be needed for a stronger tamper-evidence claim.

## Make review atomic

A write begins with `BEGIN IMMEDIATE`. The server checks the expected case version, validates membership and candidate validity, inserts a resolution, and appends the audit event in the same transaction. Any error rolls the whole change back. A second reviewer with stale state receives a conflict instead of overwriting a completed decision.

Readers use one snapshot for evidence and decisions. SQLite WAL allows readers alongside the bounded single-writer workflow. The database must live on a local disk-backed volume; do not put its WAL files on a shared network filesystem or run multiple replicas against the same file.

## Make retry behavior explicit

The pair of source hashes plus rule version is the ingestion idempotency key. Retrying an identical import returns the existing run instead of creating duplicate work. Source order matters: swapping the scorer and league changes the evidence's meaning.

Decision reuse is a separate operation from ingestion idempotency. It applies an earlier human-reviewed choice to a matching case in a different release. The evidence fingerprint must match exactly. The synthetic follow-up release keeps seven cases unchanged and changes one case, which demonstrates the boundary.

## Show incomplete coverage honestly

The denominator for publishable coverage is all unique game groups. Automatically matched and valid human-selected games count toward the numerator. Open cases and quarantined games do not.

Closing the queue permits export, but it does not imply every input game was published. The manifest explicitly lists exclusions and labels completeness. Standings are a view over published final games under the stated demo points policy; they cannot silently count quarantined data.

## Why SQLite and a single service

The scope is a reproducible, bounded, local review workbench. A pure engine plus FastAPI, SQLite, and one static frontend can be understood, tested, and run without cloud credentials. Additional services would not improve the integrity argument at this scale.

For a shared service, the next changes are authenticated reviewers and authorization, PostgreSQL with equivalent transaction guarantees, immutable object storage for source files, asynchronous ingestion for larger jobs, and operational backup/restore procedures. The optional Azure archive already works at the export boundary without pretending these broader deployment concerns are solved.

## Complexity and measurement

Parsing and hash grouping are linear in input size. Stable ordering of output groups and per-case evidence adds sorting costs. Memory is proportional to retained source records and their raw/normalized evidence. Limits cap each source at 5,000 rows and 1 MiB.

Run a repeatable benchmark:

```sh
.venv/bin/python scripts/benchmark.py --games 1000 --repeats 5
```

The script generates paired sources, deliberately changes one in twenty games, asserts the expected number of cases, warms the engine, measures multiple runs, and separately measures traced Python allocations. It excludes HTTP, SQLite, rendering, and network latency. Record the printed environment and input sizes alongside any quoted result; do not present it as cloud throughput.

## Technology references

- [FastAPI lifespan-aware tests](https://fastapi.tiangolo.com/advanced/testing-events/)
- [SQLite transaction semantics](https://www.sqlite.org/lang_transaction.html)
- [Vite documentation](https://vite.dev/guide/)

The repository pins the dependencies used for this implementation. The interactive API schema at `/docs` describes the running HTTP interface.
