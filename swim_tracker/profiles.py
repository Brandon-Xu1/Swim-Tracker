"""Profiles derived exclusively from visible imported results."""

import hashlib
import json
import math
import uuid

import pandas as pd
from sqlalchemy import bindparam, text

from . import database as db
from .accounts import require_role
from .storage import goals, identities, result_profiles, swimmers, transaction


def identity_key(row):
    # Source-specific links make manual corrections survive re-imports.
    return hashlib.sha256(
        json.dumps([row["source_file"], row["athlete_id"], row["name"], row["gender"]]).encode()
    ).hexdigest()


def link_rows(c, team_id, rows):
    known = dict(
        c.execute(
            text("SELECT identity_key, swimmer_id FROM swimmer_identities WHERE team_id=:team"),
            {"team": team_id},
        ).all()
    )
    matches = c.execute(
        text(
            "SELECT DISTINCT r.athlete_id, r.name, r.gender, p.swimmer_id FROM results r "
            "JOIN result_profiles p ON r.id=p.result_id WHERE r.team_id=:team"
        ),
        {"team": team_id},
    ).all()
    candidates = {}
    for athlete, name, gender, profile in matches:
        if athlete:
            candidates.setdefault((athlete, name.casefold(), gender), set()).add(profile)
    links = []
    for row in rows:
        key = identity_key(row)
        if key not in known:
            possible = (
                candidates.get((row["athlete_id"], row["name"].casefold(), row["gender"]), set())
                if row["athlete_id"]
                else set()
            )
            # Names alone are never matched; conflicting identifiers require review.
            profile_id = next(iter(possible)) if len(possible) == 1 else uuid.uuid4().hex
            if len(possible) != 1:
                c.execute(
                    swimmers.insert().values(id=profile_id, team_id=team_id, name=row["name"])
                )
            c.execute(
                identities.insert().values(team_id=team_id, identity_key=key, swimmer_id=profile_id)
            )
            known[key] = profile_id
            if row["athlete_id"]:
                candidates.setdefault(
                    (row["athlete_id"], row["name"].casefold(), row["gender"]), set()
                ).add(profile_id)
        links.append({"result_id": row["id"], "swimmer_id": known[key]})
    if links:
        c.execute(result_profiles.insert(), links)


def ensure_profiles(target, team_ids):
    db.initialize_database(target)
    for team_id in team_ids:
        with transaction(target, f"team:{team_id}") as c:
            rows = (
                c.execute(
                    text(
                        "SELECT r.* FROM results r LEFT JOIN result_profiles p ON p.result_id=r.id "
                        "WHERE r.team_id=:team AND p.result_id IS NULL"
                    ),
                    {"team": team_id},
                )
                .mappings()
                .all()
            )
            link_rows(c, team_id, rows)


def directory(target, team_ids, name=""):
    ensure_profiles(target, team_ids)
    query = text(
        'SELECT s.id, s.team_id, s.name AS "Swimmer", COUNT(*) AS "Results", '
        'COUNT(DISTINCT r.source_file) AS "Meets", MIN(r.meet_date) AS "First swim", '
        'MAX(r.meet_date) AS "Latest swim" FROM swimmers s '
        "JOIN result_profiles p ON p.swimmer_id=s.id JOIN results r ON r.id=p.result_id "
        "WHERE s.team_id IN :teams AND LOWER(s.name) LIKE :name "
        "GROUP BY s.id, s.team_id, s.name ORDER BY LOWER(s.name), s.id LIMIT 501"
    ).bindparams(bindparam("teams", expanding=True))
    with db._engine(target).connect() as c:
        return pd.read_sql_query(
            query, c, params={"teams": list(team_ids), "name": "%" + name.lower().strip() + "%"}
        )


def history(target, profile_id, team_ids):
    db.initialize_database(target)
    query = text(
        'SELECT r.id, r.name AS "Name", r.event AS "Event", r.course AS "Course", '
        'r.meet_date AS "Date", r.time_seconds AS "Seconds", r.time AS "Time", '
        'r.source_file AS "Meet", r.source_row AS "Source row" '
        "FROM results r JOIN result_profiles p ON p.result_id=r.id "
        "JOIN swimmers s ON s.id=p.swimmer_id "
        "WHERE s.id=:profile AND s.team_id IN :teams AND r.team_id=s.team_id "
        "ORDER BY r.meet_date, r.id"
    ).bindparams(bindparam("teams", expanding=True))
    with db._engine(target).connect() as c:
        frame = pd.read_sql_query(query, c, params={"profile": profile_id, "teams": list(team_ids)})
    frame["Course"] = frame["Course"].map(lambda value: db.COURSE_LABELS.get(value, value))
    return frame


def format_time(seconds):
    # Integer hundredths avoids formatting 59.999 as 0:60.00.
    hundredths = round(float(seconds) * 100)
    minutes, rest = divmod(hundredths, 6000)
    return f"{minutes}:{rest / 100:05.2f}" if minutes else f"{rest / 100:.2f}"


def best_times(frame):
    if frame.empty:
        return frame.copy()
    return frame.loc[
        frame.groupby(["Event", "Course"])["Seconds"].idxmin(),
        ["Event", "Course", "Time", "Date", "Meet"],
    ].reset_index(drop=True)


def progression(frame, event, course):
    subset = frame[(frame["Event"] == event) & (frame["Course"] == course)].copy()
    if subset.empty:
        return subset
    daily = subset.groupby("Date", as_index=False)["Seconds"].min().sort_values("Date")
    daily["Best so far"] = daily["Seconds"].cummin()
    return daily


def _editable(c, account, profile_id):
    team = c.execute(text("SELECT team_id FROM swimmers WHERE id=:id"), {"id": profile_id}).scalar()
    if team is None:
        raise ValueError("Swimmer profile not found.")
    require_role(c, account, team)
    return team


def set_goal(target, account, profile_id, event, course, seconds):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Goal time must be positive.")
    with db._engine(target).connect() as c:
        team_id = _editable(c, account, profile_id)
    with transaction(target, f"team:{team_id}") as c:
        _editable(c, account, profile_id)
        c.execute(
            text(
                "INSERT INTO swimmer_goals (swimmer_id, event, course, seconds) VALUES (:id, :event, :course, :seconds) "
                "ON CONFLICT (swimmer_id, event, course) DO UPDATE SET seconds=excluded.seconds"
            ),
            {"id": profile_id, "event": event, "course": course, "seconds": seconds},
        )


def get_goal(target, profile_id, event, course, team_ids):
    query = text(
        "SELECT g.seconds FROM swimmer_goals g JOIN swimmers s ON s.id=g.swimmer_id "
        "WHERE s.id=:id AND s.team_id IN :teams AND g.event=:event AND g.course=:course"
    ).bindparams(bindparam("teams", expanding=True))
    with db._engine(target).connect() as c:
        return c.execute(
            query, {"id": profile_id, "teams": list(team_ids), "event": event, "course": course}
        ).scalar()


def merge(target, account, source_id, destination_id):
    if source_id == destination_id:
        raise ValueError("Choose two different profiles.")
    # All profile edits and imports use the same team lock.
    with db._engine(target).connect() as c:
        team_id = _editable(c, account, source_id)
    with transaction(target, f"team:{team_id}") as c:
        if _editable(c, account, source_id) != _editable(c, account, destination_id):
            raise ValueError("Profiles must belong to the same team.")
        c.execute(
            text("UPDATE result_profiles SET swimmer_id=:dest WHERE swimmer_id=:src"),
            {"dest": destination_id, "src": source_id},
        )
        c.execute(
            text("UPDATE swimmer_identities SET swimmer_id=:dest WHERE swimmer_id=:src"),
            {"dest": destination_id, "src": source_id},
        )
        for row in (
            c.execute(text("SELECT * FROM swimmer_goals WHERE swimmer_id=:id"), {"id": source_id})
            .mappings()
            .all()
        ):
            c.execute(
                text(
                    "INSERT INTO swimmer_goals (swimmer_id, event, course, seconds) VALUES (:id, :event, :course, :seconds) "
                    "ON CONFLICT (swimmer_id, event, course) DO NOTHING"
                ),
                {
                    "id": destination_id,
                    "event": row["event"],
                    "course": row["course"],
                    "seconds": row["seconds"],
                },
            )
        c.execute(goals.delete().where(goals.c.swimmer_id == source_id))
        c.execute(swimmers.delete().where(swimmers.c.id == source_id))


def separate_meet(target, account, profile_id, source_file):
    with db._engine(target).connect() as c:
        team_id = _editable(c, account, profile_id)
    with transaction(target, f"team:{team_id}") as c:
        _editable(c, account, profile_id)
        rows = (
            c.execute(
                text(
                    "SELECT r.* FROM results r JOIN result_profiles p ON r.id=p.result_id "
                    "WHERE p.swimmer_id=:id AND r.source_file=:source AND r.team_id=:team"
                ),
                {"id": profile_id, "source": source_file, "team": team_id},
            )
            .mappings()
            .all()
        )
        if not rows:
            raise ValueError("No matching meet in this profile.")
        new_id = uuid.uuid4().hex
        c.execute(swimmers.insert().values(id=new_id, team_id=team_id, name=rows[0]["name"]))
        for row in rows:
            c.execute(
                text("UPDATE result_profiles SET swimmer_id=:new WHERE result_id=:id"),
                {"new": new_id, "id": row["id"]},
            )
            c.execute(
                text(
                    "UPDATE swimmer_identities SET swimmer_id=:new WHERE team_id=:team AND identity_key=:key"
                ),
                {"new": new_id, "team": team_id, "key": identity_key(row)},
            )
        return new_id
