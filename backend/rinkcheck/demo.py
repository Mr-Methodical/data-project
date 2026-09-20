"""Reproducible, explicitly synthetic hockey exports for the demo workflow.

No external API, random state, real player or league data is involved. 24 game
identities give 16 auto matches + 8 review groups in the opening release. The
followup changes ONE disputed score's evidence; the other seven cases can reuse
exactly reviewed decisions. The clean variant has 24 matching groups.
"""

from __future__ import annotations

import copy
import csv
import io
from datetime import date, timedelta

from .rules import COLUMNS

TEAMS = (
    "Rivergate Comets", "Northfield Foxes", "Millpond Owls",
    "Maplebridge Meteors", "Stonehaven Sparks", "Cedarvale Drift",
)
DESCRIPTIONS = {
    "opening": "Synthetic opening release: 24 game identities, 16 automatic matches and 8 review cases. Contains deliberate score/shot conflicts, missing records, invalid statistics and a conflicting duplicate. Teams, games and venues are fictional.",
    "followup": "Synthetic followup: one disputed score changed; seven cases retain exactly the same source evidence. Review opening first, then replay decisions here to see exact-evidence reuse without trusting changed data.",
    "clean": "Synthetic clean release: 24 valid matching game identities, including one scheduled game without statistics. All results can be exported without review; this is a clean-data comparison, not a corrected production release.",
}


def demo_description(variant: str = "opening") -> str:
    try:
        return DESCRIPTIONS[variant]
    except KeyError as exc:
        raise ValueError("Unknown demo variant; use opening, followup or clean.") from exc


def _csv(rows: list[dict]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _base_games() -> list[dict]:
    games = []
    # A deterministic mini schedule, not intended as a full balanced season.
    for index in range(24):
        home = index % len(TEAMS)
        away = (home + 1 + index // len(TEAMS)) % len(TEAMS)
        home_goals, away_goals = (index * 3 + 2) % 6, (index * 2 + 1) % 5
        games.append({
            "season": "2026-27", "game_id": f"RC-{1001 + index}",
            "game_date": (date(2026, 9, 4) + timedelta(days=index // 3)).isoformat(),
            "home_team": TEAMS[home], "away_team": TEAMS[away],
            "home_goals": str(home_goals), "away_goals": str(away_goals),
            "home_shots": str(24 + index % 12), "away_shots": str(21 + index * 3 % 14),
            "status": "final", "venue": f"{TEAMS[home].split()[0]} Ice Hall",
            "updated_at": "2026-09-18T18:00:00Z",
        })
    # Same team pair/date does not imply same game identity (e.g. doubleheader).
    for field in ("game_date", "home_team", "away_team", "venue"):
        games[22][field] = games[7][field]
    games[23]["status"] = "scheduled"
    for field in ("home_goals", "away_goals", "home_shots", "away_shots"):
        games[23][field] = ""
    return games


def demo_pair(variant: str = "opening") -> tuple[str, str]:
    demo_description(variant)
    scorer = _base_games()
    league = copy.deepcopy(scorer)
    # Feed publication time is evidence, but not a substantive game difference.
    for row in league:
        row["updated_at"] = "2026-09-18T18:05:00Z"
    if variant == "clean":
        return _csv(scorer), _csv(league)

    league[1]["home_goals"] = "1"  # scorer says 5; followup changes to 2
    league[4]["away_shots"] = "35"
    league[10]["home_shots"] = "NaN"
    league[16]["home_shots"] = "1"  # home goals 2, impossible shot count
    league[19]["away_team"] = league[19]["home_team"]

    # Explicit aliases and whitespace are safe, transparent auto-normalizations.
    league[2]["home_team"] = "M. Owls"
    league[6]["home_team"] = "  Rivergate   Comets  "
    league[6]["venue"] = "Rivergate\u00a0Ice Hall"
    league[9]["status"] = "FINAL"
    league[12]["home_team"] = "Rivergate C."

    conflicting = copy.deepcopy(scorer[13])
    conflicting["away_goals"] = "4"
    conflicting["updated_at"] = "2026-09-18T18:01:00Z"
    scorer.append(conflicting)
    league.append(copy.deepcopy(league[11]))  # retained duplicate provenance
    scorer = [row for row in scorer if row["game_id"] != "RC-1023"]
    league = [row for row in league if row["game_id"] != "RC-1008"]
    if variant == "followup":
        for row in league:
            if row["game_id"] == "RC-1002":
                row["home_goals"] = "2"
                row["updated_at"] = "2026-09-19T10:00:00Z"
    return _csv(scorer), _csv(league)
