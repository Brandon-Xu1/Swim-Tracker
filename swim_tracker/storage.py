"""Additive tables and serialized transactions for application services."""

import json
import time
from contextlib import contextmanager

from sqlalchemy import Column, Float, Integer, Table, Text, UniqueConstraint, text

from . import database as db
from .rate_limit import RateLimitedError, SlidingWindowLimit, try_acquire

users = Table(
    "users",
    db.metadata,
    Column("id", Integer, primary_key=True),
    Column("username", Text, nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("recovery_hash", Text, nullable=False),
    Column("session_version", Integer, nullable=False, server_default="1"),
)
memberships = Table(
    "memberships",
    db.metadata,
    Column("user_id", Integer, primary_key=True),
    Column("team_id", Integer, primary_key=True),
    Column("role", Text, nullable=False),
)
invitations = Table(
    "invitations",
    db.metadata,
    Column("token_hash", Text, primary_key=True),
    Column("team_id", Integer, nullable=False),
    Column("role", Text, nullable=False),
    Column("expires_at", Float, nullable=False),
)
quotas = Table(
    "quotas",
    db.metadata,
    Column("key", Text, primary_key=True),
    Column("history", Text, nullable=False),
)
locks = Table("service_locks", db.metadata, Column("key", Text, primary_key=True))
imports = Table(
    "imports",
    db.metadata,
    Column("id", Text, primary_key=True),
    Column("team_id", Integer, nullable=False),
    Column("filename", Text, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("uploaded_at", Text, nullable=False),
    UniqueConstraint("team_id", "filename"),
    UniqueConstraint("team_id", "fingerprint"),
)
swimmers = Table(
    "swimmers",
    db.metadata,
    Column("id", Text, primary_key=True),
    Column("team_id", Integer, nullable=False, index=True),
    Column("name", Text, nullable=False),
)
identities = Table(
    "swimmer_identities",
    db.metadata,
    Column("team_id", Integer, primary_key=True),
    Column("identity_key", Text, primary_key=True),
    Column("swimmer_id", Text, nullable=False, index=True),
)
result_profiles = Table(
    "result_profiles",
    db.metadata,
    Column("result_id", Integer, primary_key=True),
    Column("swimmer_id", Text, nullable=False, index=True),
)
goals = Table(
    "swimmer_goals",
    db.metadata,
    Column("swimmer_id", Text, primary_key=True),
    Column("event", Text, primary_key=True),
    Column("course", Text, primary_key=True),
    Column("seconds", Float, nullable=False),
)


@contextmanager
def transaction(target, key="write"):
    """Serialize service writes across processes; rollback every part on error."""
    db.initialize_database(target)
    with db._engine(target).connect() as connection:
        try:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.execute(
                    text(
                        "INSERT INTO service_locks (key) VALUES (:key) ON CONFLICT (key) DO NOTHING"
                    ),
                    {"key": key},
                )
                connection.execute(
                    text("SELECT key FROM service_locks WHERE key=:key FOR UPDATE"), {"key": key}
                )
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def acquire_quotas(target, buckets, *, now=None):
    """Atomically reserve all quotas, persisted across browser/server restarts."""
    now = time.time() if now is None else now
    with transaction(target, "quotas") as connection:
        histories = {}
        waits = []
        for key, limits in buckets:
            raw = connection.execute(
                text("SELECT history FROM quotas WHERE key=:key"), {"key": key}
            ).scalar()
            history = json.loads(raw) if raw else []
            waits.append(try_acquire(limits, history, now))
            histories[key] = history
        if max(waits, default=0) > 0:
            raise RateLimitedError(max(waits))
        # Prune expired rows so arbitrary login names cannot grow this table forever.
        if int(now) % 60 == 0:
            cutoff = now - 86400
            rows = connection.execute(text("SELECT key, history FROM quotas")).all()
            for key, raw in rows:
                if key not in histories and not any(t > cutoff for t in json.loads(raw)):
                    connection.execute(text("DELETE FROM quotas WHERE key=:key"), {"key": key})
        for key, history in histories.items():
            connection.execute(
                text(
                    "INSERT INTO quotas (key, history) VALUES (:key, :history) "
                    "ON CONFLICT (key) DO UPDATE SET history=excluded.history"
                ),
                {"key": key, "history": json.dumps(history)},
            )


def limit_login(target, username):
    import hashlib

    key = hashlib.sha256(username.strip().lower().encode()).hexdigest()
    acquire_quotas(
        target,
        [
            ("login:" + key, [SlidingWindowLimit(8, 300)]),
            ("login:global", [SlidingWindowLimit(200, 60)]),
        ],
    )
