"""Reproducible in-process reconciliation benchmark, not a cloud load test."""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from rinkcheck.engine import analyze  # noqa: E402


def pair(games: int) -> tuple[str, str, int]:
    with (ROOT / "fixtures/clean_scorer.csv").open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames
        base = next(row for row in reader if row["status"] == "final")
    left, right = [], []
    conflicts = 0
    for index in range(games):
        scorer = {**base, "game_id": f"BENCH-{index:05d}"}
        league = dict(scorer)
        if index % 20 == 0:
            league["home_goals"] = str(int(scorer["home_goals"]) + 1)
            league["home_shots"] = str(max(int(scorer["home_shots"]), int(league["home_goals"])))
            conflicts += 1
        left.append(scorer)
        right.append(league)

    def render(rows: list[dict]) -> str:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue()

    return render(left), render(right), conflicts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=1000, help="1–5000 unique games; two rows per game")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.games <= 5000 or not 1 <= args.repeats <= 30:
        parser.error("games must be 1–5000; repeats must be 1–30")
    scorer, league, expected = pair(args.games)
    analyze(scorer, league)  # Warm the same code path; not included in timings.
    times = []
    for _ in range(args.repeats):
        start = time.perf_counter()
        analysis = analyze(scorer, league)
        times.append(time.perf_counter() - start)
        assert analysis["stats"]["game_count"] == args.games
        assert len(analysis["cases"]) == expected
        assert len(analysis["canonical"]) + len(analysis["cases"]) == args.games
    # Measure traced Python allocations separately to avoid distorting timings.
    tracemalloc.start()
    analyze(scorer, league)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({
        "python": platform.python_version(), "platform": platform.platform(),
        "scope": "CSV parsing + normalization + reconciliation in one Python process; excludes HTTP/database/browser",
        "unique_games": args.games, "input_rows": args.games * 2, "expected_cases": expected,
        "source_bytes": [len(scorer.encode()), len(league.encode())],
        "repeats": args.repeats, "seconds": times,
        "median_seconds": statistics.median(times),
        "peak_python_allocations_mib": round(peak / 1024 ** 2, 2),
        "note": "Environment-specific measurement; traced allocations are not process RSS.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
