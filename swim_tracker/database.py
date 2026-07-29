"""SQLite persistence and safe, parameterized searches."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
import sqlite3

import pandas as pd

from .parser import SwimResult


SCHEMA_VERSION = 1
DISPLAY_COLUMNS = [
    "Name",
    "Group",
    "Event",
    "Time",
    "Date",
    "Course",
    "Time (Seconds)",
]


def connect(database_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(database_path: str | Path) -> None:
    """Create the current schema without deleting existing current-schema data."""
    with connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY,
                source_file TEXT NOT NULL,
                source_row INTEGER NOT NULL,
                athlete_id TEXT NOT NULL,
                name TEXT NOT NULL,
                age INTEGER NOT NULL,
                gender TEXT NOT NULL,
                group_label TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event TEXT NOT NULL,
                distance_yards INTEGER NOT NULL,
                stroke TEXT NOT NULL,
                time TEXT NOT NULL,
                time_seconds REAL NOT NULL,
                course TEXT NOT NULL,
                meet_date TEXT NOT NULL,
                UNIQUE(source_file, source_row)
            );

            CREATE INDEX IF NOT EXISTS results_name_idx
                ON results(name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS results_event_idx
                ON results(distance_yards, stroke);
            CREATE INDEX IF NOT EXISTS results_group_idx
                ON results(group_label);
            """
        )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def schema_is_current(database_path: str | Path) -> bool:
    path = Path(database_path)
    if not path.exists():
        return False
    with connect(path) as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(results)").fetchall()
        }
    return {
        "source_file",
        "source_row",
        "name",
        "event_id",
        "time_seconds",
        "meet_date",
    }.issubset(columns)


def rebuild_database(
    database_path: str | Path, results: Sequence[SwimResult]
) -> int:
    """Transactionally replace a legacy database with the current schema."""
    path = Path(database_path)
    temporary_path = path.with_suffix(f"{path.suffix}.new")
    if temporary_path.exists():
        temporary_path.unlink()

    initialize_database(temporary_path)
    replace_source_results(temporary_path, results)
    temporary_path.replace(path)
    return len(results)


def replace_source_results(
    database_path: str | Path, results: Sequence[SwimResult]
) -> int:
    """Replace one imported source atomically; repeated imports do not duplicate."""
    if not results:
        raise ValueError("The file does not contain any completed individual results.")

    initialize_database(database_path)
    source_file = results[0].source_file
    if any(result.source_file != source_file for result in results):
        raise ValueError("All results in one import must have the same source file.")

    columns = list(asdict(results[0]).keys())
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = (
        f"INSERT INTO results ({', '.join(columns)}) VALUES ({placeholders})"
    )

    with connect(database_path) as connection:
        connection.execute(
            "DELETE FROM results WHERE source_file = ?", (source_file,)
        )
        connection.executemany(
            insert_sql,
            ([getattr(result, column) for column in columns] for result in results),
        )
    return len(results)


def result_count(database_path: str | Path) -> int:
    initialize_database(database_path)
    with connect(database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM results").fetchone()[0])


def source_summary(database_path: str | Path) -> pd.DataFrame:
    initialize_database(database_path)
    query = """
        SELECT source_file AS "Source file",
               COUNT(*) AS "Completed results",
               MIN(meet_date) AS "First date",
               MAX(meet_date) AS "Last date"
        FROM results
        GROUP BY source_file
        ORDER BY MAX(meet_date) DESC, source_file
    """
    with connect(database_path) as connection:
        return pd.read_sql_query(query, connection)


def filter_options(database_path: str | Path) -> dict[str, list[str]]:
    initialize_database(database_path)
    with connect(database_path) as connection:
        groups = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT group_label FROM results ORDER BY group_label"
            )
        ]
        strokes = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT stroke FROM results ORDER BY stroke"
            )
        ]
        events = [
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT event FROM results
                ORDER BY distance_yards, stroke
                """
            )
        ]
    return {"groups": groups, "strokes": strokes, "events": events}


def search_results(
    database_path: str | Path,
    *,
    name: str | None = None,
    group_label: str | None = None,
    event: str | None = None,
    distance_yards: int | None = None,
    stroke: str | None = None,
    sort_order: str = "name",
    limit: int = 200,
) -> pd.DataFrame:
    """Search results with fixed SQL and bound values only."""
    clauses: list[str] = []
    parameters: list[object] = []

    if name and name.strip():
        clauses.append("name LIKE ? COLLATE NOCASE")
        parameters.append(f"%{name.strip()}%")
    if group_label:
        clauses.append("group_label = ?")
        parameters.append(group_label)
    if event:
        clauses.append("event = ?")
        parameters.append(event)
    if distance_yards is not None:
        clauses.append("distance_yards = ?")
        parameters.append(int(distance_yards))
    if stroke:
        clauses.append("stroke = ?")
        parameters.append(stroke)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order_by = (
        "time_seconds ASC, name COLLATE NOCASE ASC"
        if sort_order == "fastest"
        else "name COLLATE NOCASE ASC, distance_yards ASC, stroke ASC, meet_date ASC"
    )
    safe_limit = max(1, min(int(limit), 1000))
    parameters.append(safe_limit)

    query = f"""
        SELECT name AS "Name",
               group_label AS "Group",
               event AS "Event",
               time AS "Time",
               meet_date AS "Date",
               CASE course
                    WHEN 'Y' THEN 'SCY'
                    WHEN 'S' THEN 'SCM'
                    WHEN 'L' THEN 'LCM'
                    ELSE course
               END AS "Course",
               time_seconds AS "Time (Seconds)"
        FROM results
        {where}
        ORDER BY {order_by}
        LIMIT ?
    """

    with connect(database_path) as connection:
        return pd.read_sql_query(query, connection, params=parameters)
