"""Database persistence and safe, parameterized searches.

Two backends are supported through SQLAlchemy and selected by the target
passed to every function:

- A filesystem path (the default) uses a local SQLite file, which keeps
  development and tests dependency-free.
- A ``postgresql://`` URL uses a hosted Postgres database such as Neon, so
  imported meets survive redeploys of an ephemeral app container.

Every query uses fixed SQL with bound parameters only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import pandas as pd
from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from .parser import SwimResult


COURSE_LABELS = {"Y": "SCY", "S": "SCM", "L": "LCM"}
COURSE_CODES = {label: code for code, label in COURSE_LABELS.items()}

REQUIRED_RESULT_COLUMNS = {
    "source_file",
    "source_row",
    "name",
    "event_id",
    "time_seconds",
    "meet_date",
}

metadata = MetaData()

results_table = Table(
    "results",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_file", Text, nullable=False),
    Column("source_row", Integer, nullable=False),
    Column("athlete_id", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("age", Integer, nullable=False),
    Column("gender", Text, nullable=False),
    Column("group_label", Text, nullable=False),
    Column("event_id", Text, nullable=False),
    Column("event", Text, nullable=False),
    Column("distance_yards", Integer, nullable=False),
    Column("stroke", Text, nullable=False),
    Column("time", Text, nullable=False),
    Column("time_seconds", Float, nullable=False),
    Column("course", Text, nullable=False),
    Column("meet_date", Text, nullable=False),
    UniqueConstraint("source_file", "source_row", name="uq_results_source"),
    Index("results_name_idx", "name"),
    Index("results_event_idx", "distance_yards", "stroke"),
    Index("results_group_idx", "group_label"),
)

app_meta_table = Table(
    "app_meta",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
)


_ENGINES: dict[str, Engine] = {}


def _database_url(target: str | Path) -> str:
    value = str(target)
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value[len("postgresql://") :]
    if "://" in value:
        return value
    return f"sqlite:///{Path(value).expanduser()}"


def is_sqlite_target(target: str | Path) -> bool:
    return _database_url(target).startswith("sqlite")


def _engine(target: str | Path) -> Engine:
    url = _database_url(target)
    engine = _ENGINES.get(url)
    if engine is None:
        if url.startswith("sqlite"):
            # NullPool releases the file handle after each operation, so
            # temporary databases in tests can be deleted immediately.
            engine = create_engine(url, poolclass=NullPool)
        else:
            engine = create_engine(
                url, pool_pre_ping=True, pool_size=2, max_overflow=3
            )
        _ENGINES[url] = engine
    return engine


def _forget_engine(target: str | Path) -> None:
    engine = _ENGINES.pop(_database_url(target), None)
    if engine is not None:
        engine.dispose()


def initialize_database(target: str | Path) -> None:
    """Create any missing tables without touching existing data."""
    metadata.create_all(_engine(target))


def get_meta(target: str | Path, key: str) -> str | None:
    initialize_database(target)
    with _engine(target).connect() as connection:
        row = connection.execute(
            text("SELECT value FROM app_meta WHERE key = :key"), {"key": key}
        ).fetchone()
    return None if row is None else str(row[0])


def set_meta(target: str | Path, key: str, value: str) -> None:
    initialize_database(target)
    with _engine(target).begin() as connection:
        connection.execute(
            text(
                "INSERT INTO app_meta (key, value) VALUES (:key, :value) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value"
            ),
            {"key": key, "value": value},
        )


def schema_is_current(target: str | Path) -> bool:
    inspector = inspect(_engine(target))
    if not inspector.has_table("results"):
        return False
    columns = {column["name"] for column in inspector.get_columns("results")}
    return REQUIRED_RESULT_COLUMNS.issubset(columns)


def rebuild_database(
    target: str | Path, results: Sequence[SwimResult]
) -> int:
    """Replace a legacy or empty database with the current schema and data."""
    url = _database_url(target)
    if url.startswith("sqlite"):
        path = Path(url[len("sqlite:///") :])
        temporary_path = path.with_suffix(f"{path.suffix}.new")
        if temporary_path.exists():
            temporary_path.unlink()

        initialize_database(temporary_path)
        replace_source_results(temporary_path, results)
        _forget_engine(temporary_path)
        _forget_engine(path)
        temporary_path.replace(path)
        return len(results)

    engine = _engine(target)
    with engine.begin() as connection:
        metadata.drop_all(connection)
        metadata.create_all(connection)
    replace_source_results(target, results)
    return len(results)


def replace_source_results(
    target: str | Path, results: Sequence[SwimResult]
) -> int:
    """Replace one imported source atomically; repeated imports do not duplicate."""
    if not results:
        raise ValueError("The file does not contain any completed individual results.")

    initialize_database(target)
    source_file = results[0].source_file
    if any(result.source_file != source_file for result in results):
        raise ValueError("All results in one import must have the same source file.")

    columns = list(asdict(results[0]).keys())
    insert_sql = text(
        f"INSERT INTO results ({', '.join(columns)}) "
        f"VALUES ({', '.join(f':{column}' for column in columns)})"
    )

    with _engine(target).begin() as connection:
        connection.execute(
            text("DELETE FROM results WHERE source_file = :source_file"),
            {"source_file": source_file},
        )
        connection.execute(insert_sql, [asdict(result) for result in results])
    return len(results)


def delete_source_results(target: str | Path, source_file: str) -> int:
    """Remove every result imported from one source file."""
    initialize_database(target)
    with _engine(target).begin() as connection:
        outcome = connection.execute(
            text("DELETE FROM results WHERE source_file = :source_file"),
            {"source_file": source_file},
        )
        return outcome.rowcount


def result_count(target: str | Path) -> int:
    initialize_database(target)
    with _engine(target).connect() as connection:
        return int(
            connection.execute(text("SELECT COUNT(*) FROM results")).scalar_one()
        )


def source_summary(target: str | Path) -> pd.DataFrame:
    initialize_database(target)
    query = text(
        """
        SELECT source_file AS "Source file",
               COUNT(*) AS "Completed results",
               MIN(meet_date) AS "First date",
               MAX(meet_date) AS "Last date"
        FROM results
        GROUP BY source_file
        ORDER BY MAX(meet_date) DESC, source_file
        """
    )
    with _engine(target).connect() as connection:
        return pd.read_sql_query(query, connection)


def filter_options(target: str | Path) -> dict[str, object]:
    initialize_database(target)
    with _engine(target).connect() as connection:
        groups = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT DISTINCT group_label FROM results "
                    "ORDER BY group_label"
                )
            )
        ]
        strokes = [
            row[0]
            for row in connection.execute(
                text("SELECT DISTINCT stroke FROM results ORDER BY stroke")
            )
        ]
        events = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT event FROM results GROUP BY event "
                    "ORDER BY MIN(distance_yards), MIN(stroke)"
                )
            )
        ]
        course_codes = {
            row[0]
            for row in connection.execute(
                text("SELECT DISTINCT course FROM results")
            )
        }
        courses = [
            label
            for code, label in COURSE_LABELS.items()
            if code in course_codes
        ] + sorted(course_codes - COURSE_LABELS.keys())
        date_row = connection.execute(
            text("SELECT MIN(meet_date), MAX(meet_date) FROM results")
        ).fetchone()
    return {
        "groups": groups,
        "strokes": strokes,
        "events": events,
        "courses": courses,
        "date_range": tuple(date_row) if date_row and date_row[0] else None,
    }


def search_results(
    target: str | Path,
    *,
    name: str | None = None,
    group_label: str | None = None,
    event: str | None = None,
    distance_yards: int | None = None,
    stroke: str | None = None,
    course: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort_order: str = "name",
    limit: int = 200,
) -> pd.DataFrame:
    """Search results with fixed SQL and bound values only."""
    clauses: list[str] = []
    parameters: dict[str, object] = {}

    if name and name.strip():
        clauses.append("LOWER(name) LIKE :name")
        parameters["name"] = f"%{name.strip().lower()}%"
    if group_label:
        clauses.append("group_label = :group_label")
        parameters["group_label"] = group_label
    if event:
        clauses.append("event = :event")
        parameters["event"] = event
    if distance_yards is not None:
        clauses.append("distance_yards = :distance_yards")
        parameters["distance_yards"] = int(distance_yards)
    if stroke:
        clauses.append("stroke = :stroke")
        parameters["stroke"] = stroke
    if course:
        if course not in COURSE_CODES:
            raise ValueError(f"Unsupported course: {course!r}")
        clauses.append("course = :course")
        parameters["course"] = COURSE_CODES[course]
    if date_from:
        clauses.append("meet_date >= :date_from")
        parameters["date_from"] = date_from
    if date_to:
        clauses.append("meet_date <= :date_to")
        parameters["date_to"] = date_to

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order_by = (
        "time_seconds ASC, LOWER(name) ASC"
        if sort_order == "fastest"
        else "LOWER(name) ASC, distance_yards ASC, stroke ASC, meet_date ASC"
    )
    parameters["limit"] = max(1, min(int(limit), 1000))

    query = text(
        f"""
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
        LIMIT :limit
        """
    )

    with _engine(target).connect() as connection:
        return pd.read_sql_query(query, connection, params=parameters)
