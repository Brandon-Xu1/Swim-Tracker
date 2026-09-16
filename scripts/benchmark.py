"""Deterministic synthetic benchmark; never touches the application's database."""

import argparse
import json
import math
import platform
import statistics
import tempfile
import time
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from swim_tracker import database as db
from swim_tracker.parser import SwimResult


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def run(rows, queries):
    if rows < 1 or queries < 1:
        raise ValueError("Rows and queries must be positive.")
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "benchmark.db"
        db.initialize_database(target)
        start = time.perf_counter()
        with db._engine(target).begin() as c:
            for offset in range(0, rows, 2000):
                batch = []
                for i in range(offset, min(offset + 2000, rows)):
                    result = SwimResult(
                        f"meet-{i // 4000}.cl2",
                        i + 1,
                        f"synthetic-{i % 2000}",
                        f"Swimmer {i % 2000:04d}",
                        12,
                        "F",
                        "Girls 11-12",
                        "1001",
                        "100-yard Free",
                        100,
                        "Freestyle",
                        "60.00",
                        50 + (i % 3000) / 100,
                        "Y",
                        (date(2024, 1, 1) + timedelta(days=i // 4000)).isoformat(),
                    )
                    batch.append({"team_id": 0, **asdict(result)})
                c.execute(db.results_table.insert(), batch)
        ingest = time.perf_counter() - start
        timings = {"name_search": [], "event_ranking": []}
        for _ in range(3):
            db.search_results(target, name="Swimmer 0100")
        for i in range(queries):
            for kind, filters in (
                ("name_search", {"name": f"Swimmer {i % 2000:04d}"}),
                (
                    "event_ranking",
                    {"distance_yards": 100, "course": "SCY", "sort_order": "fastest"},
                ),
            ):
                start = time.perf_counter()
                db.search_results(target, **filters, limit=50)
                timings[kind].append((time.perf_counter() - start) * 1000)
        output = {
            "dataset": "synthetic; one public team; 2,000 synthetic athlete IDs; 100 SCY freestyle",
            "rows": rows,
            "queries_per_type": queries,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "backend": "local SQLite",
            "insert_seconds": round(ingest, 3),
            "measurement": "in-process SQL + DataFrame, including total-match count; warm cache; sequential; excludes browser/network",
            "results": {
                kind: {
                    "median_ms": round(statistics.median(values), 2),
                    "p95_ms": round(percentile(values, 0.95), 2),
                }
                for kind, values in timings.items()
            },
        }
        db._forget_engine(target)
        return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--queries", type=int, default=40)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.rows, args.queries)
    report = json.dumps(result, indent=2) + "\n"
    print(report)
    if args.output:
        args.output.write_text(report)
