# Synthetic fixture provenance

Every record in this folder was generated deterministically by
`backend/rinkcheck/demo.py` for RinkCheck. Teams, venues and results are fictional;
there are no real player records, scraped datasets or league affiliations.

The scorer and league exports represent two intentionally imperfect operational
feeds. They are not claims about any actual hockey organization.

| Pair | Game identities | Automatic matches | Review cases |
| --- | ---: | ---: | ---: |
| `opening_*` | 24 | 16 | 8 |
| `followup_*` | 24 | 16 | 8 |
| `clean_*` | 24 | 24 | 0 |

The opening pair contains 48 source rows: one identical league duplicate and one
conflicting scorer duplicate offset two missing records. Missing games RC-1008
and RC-1023 share teams/date but have different IDs. They must **not** be joined.

| Game | Deliberate review condition |
| --- | --- |
| RC-1002 | Home score conflict |
| RC-1005 | Away shot-count conflict |
| RC-1008 | Missing from league export |
| RC-1011 | Invalid numeric value `NaN` |
| RC-1014 | Divergent duplicate in scorer export |
| RC-1017 | Goals exceed shots |
| RC-1020 | Identical home and away team |
| RC-1023 | Missing from scorer export |

RC-1012 includes an exact duplicate, with all rows retained in provenance. Explicit
aliases, whitespace and capitalization produce five documented normalizations.
RC-1024 is scheduled and has no statistics; it never contributes to standings.

`followup_*` changes only RC-1002's disputed league score and timestamp. Review all
opening cases and then replay against followup: seven original human decisions
can be reused, while changed evidence must receive a new review. This is a
demonstration of exact-evidence decision reuse, not a claim that one feed is truth.

`clean_*` is the independent clean-data example. It is **not** a generated
resolution of the opening pair. `template.csv` includes one fictional example
row; replace it with records from both exports before importing.

Standings policy is deliberately simple: win = 2 points, tie = 1, loss = 0.
No overtime, shootouts, penalty minutes or external league rules are modeled.

Regenerate the six pairs from the repository root:

```bash
PYTHONPATH=backend python - <<'PY'
from pathlib import Path
from rinkcheck.demo import demo_pair

for variant in ("opening", "followup", "clean"):
    for source, content in zip(("scorer", "league"), demo_pair(variant)):
        Path(f"fixtures/{variant}_{source}.csv").write_text(content, encoding="utf-8")
PY
```
