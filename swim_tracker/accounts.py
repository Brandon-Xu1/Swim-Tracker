"""Individual identities, role checks, one-time invitations and recovery codes."""

import hashlib
import re
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import text

from . import database as db
from .auth import _normalized_team_name, hash_password, verify_password
from .storage import invitations, limit_login, memberships, transaction, users


@dataclass(frozen=True)
class Account:
    id: int
    username: str
    version: int


def _digest(value):
    return hashlib.sha256(value.strip().encode()).hexdigest()


def _password(value):
    if not 10 <= len(value) <= 256:
        raise ValueError("Use a password between 10 and 256 characters.")
    return hash_password(value)


def register(target, username, password):
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,40}", username):
        raise ValueError("Username: 3–40 letters, numbers, periods, underscores or hyphens.")
    limit_login(target, "register:" + username)
    encoded = _password(password)
    recovery = secrets.token_urlsafe(24)
    with transaction(target, "accounts") as c:
        if c.execute(text("SELECT id FROM users WHERE username=:name"), {"name": username}).first():
            raise ValueError("That username is already registered.")
        result = c.execute(
            users.insert().values(
                username=username,
                password_hash=encoded,
                recovery_hash=_digest(recovery),
                session_version=1,
            )
        )
        return Account(int(result.inserted_primary_key[0]), username, 1), recovery


_DUMMY_PASSWORD_HASH = hash_password("unused-login-placeholder")


def login(target, username, password):
    limit_login(target, username)
    with db._engine(target).connect() as c:
        row = (
            c.execute(
                text("SELECT * FROM users WHERE username=:name"), {"name": username.strip().lower()}
            )
            .mappings()
            .first()
        )
    # Equal-cost verification for missing usernames.
    encoded = row["password_hash"] if row else _DUMMY_PASSWORD_HASH
    valid = verify_password(password[:257], encoded)
    if not row or not valid:
        return None
    return Account(row["id"], row["username"], row["session_version"])


def current(target, user_id, version):
    db.initialize_database(target)
    with db._engine(target).connect() as c:
        row = c.execute(
            text("SELECT id, username, session_version FROM users WHERE id=:id"), {"id": user_id}
        ).first()
    if row and row.session_version == version:
        return Account(row.id, row.username, row.session_version)
    return None


def require_account(c, account):
    if (
        account is None
        or not c.execute(
            text("SELECT id FROM users WHERE id=:id AND session_version=:version"),
            {"id": account.id, "version": account.version},
        ).first()
    ):
        raise PermissionError("Please sign in again.")


def require_role(c, account, team_id, allowed=("owner", "coach")):
    require_account(c, account)
    role = c.execute(
        text("SELECT role FROM memberships WHERE user_id=:user AND team_id=:team"),
        {"user": account.id, "team": team_id},
    ).scalar()
    if role not in allowed:
        raise PermissionError("Your account does not have permission for this team.")
    return role


def teams_for(target, account):
    db.initialize_database(target)
    with db._engine(target).connect() as c:
        require_account(c, account)
        return (
            c.execute(
                text(
                    "SELECT t.id, t.name, m.role FROM teams t JOIN memberships m ON t.id=m.team_id "
                    "WHERE m.user_id=:id ORDER BY t.name"
                ),
                {"id": account.id},
            )
            .mappings()
            .all()
        )


def create_team(target, account, name):
    name = _normalized_team_name(name)
    with transaction(target, "accounts") as c:
        require_account(c, account)
        if c.execute(
            text("SELECT id FROM teams WHERE name_key=:key"), {"key": name.lower()}
        ).first():
            raise ValueError(
                "That team name already exists. Join with an invitation or migrate its legacy account."
            )
        result = c.execute(
            db.teams_table.insert().values(
                name=name, name_key=name.lower(), password_hash="", created_at=db._utc_now_iso()
            )
        )
        team_id = int(result.inserted_primary_key[0])
        c.execute(memberships.insert().values(user_id=account.id, team_id=team_id, role="owner"))
        return team_id


def claim_legacy_team(target, account, name, password):
    limit_login(target, "legacy:" + name)
    with transaction(target, "accounts") as c:
        require_account(c, account)
        row = (
            c.execute(
                text("SELECT * FROM teams WHERE name_key=:key"), {"key": name.strip().lower()}
            )
            .mappings()
            .first()
        )
        if not row or not verify_password(password, row["password_hash"]):
            raise ValueError(
                "Team name or legacy password is incorrect, or the team has already migrated."
            )
        if c.execute(
            text("SELECT user_id FROM memberships WHERE team_id=:team"), {"team": row["id"]}
        ).first():
            raise ValueError("This team already uses individual accounts.")
        c.execute(memberships.insert().values(user_id=account.id, team_id=row["id"], role="owner"))
        c.execute(text("UPDATE teams SET password_hash='' WHERE id=:id"), {"id": row["id"]})
        return row["id"]


def invite(target, account, team_id, role):
    if role not in ("coach", "viewer"):
        raise ValueError("Choose coach or viewer.")
    token = secrets.token_urlsafe(24)
    with transaction(target, "accounts") as c:
        require_role(c, account, team_id, ("owner",))
        c.execute(
            invitations.insert().values(
                token_hash=_digest(token),
                team_id=team_id,
                role=role,
                expires_at=time.time() + 7 * 86400,
            )
        )
    return token


def join(target, account, token):
    limit_login(target, "invite:" + str(account.id))
    with transaction(target, "accounts") as c:
        require_account(c, account)
        row = (
            c.execute(
                text("SELECT * FROM invitations WHERE token_hash=:key"), {"key": _digest(token)}
            )
            .mappings()
            .first()
        )
        if not row or row["expires_at"] < time.time():
            raise ValueError("Invitation is invalid, expired, or already used.")
        if c.execute(
            text("SELECT role FROM memberships WHERE user_id=:user AND team_id=:team"),
            {"user": account.id, "team": row["team_id"]},
        ).first():
            raise ValueError("You already belong to this team.")
        c.execute(
            memberships.insert().values(
                user_id=account.id, team_id=row["team_id"], role=row["role"]
            )
        )
        c.execute(text("DELETE FROM invitations WHERE token_hash=:key"), {"key": _digest(token)})
        return row["team_id"]


def members(target, account, team_id):
    with db._engine(target).connect() as c:
        require_role(c, account, team_id, ("owner",))
        return (
            c.execute(
                text(
                    "SELECT u.id, u.username, m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                    "WHERE m.team_id=:team ORDER BY u.username"
                ),
                {"team": team_id},
            )
            .mappings()
            .all()
        )


def set_role(target, account, team_id, user_id, role):
    if role not in ("coach", "viewer", "remove"):
        raise ValueError("Unsupported role.")
    with transaction(target, "accounts") as c:
        require_role(c, account, team_id, ("owner",))
        old = c.execute(
            text("SELECT role FROM memberships WHERE team_id=:team AND user_id=:user"),
            {"team": team_id, "user": user_id},
        ).scalar()
        if old == "owner":
            raise ValueError("The team owner cannot be removed or demoted.")
        if role == "remove":
            c.execute(
                text("DELETE FROM memberships WHERE team_id=:team AND user_id=:user"),
                {"team": team_id, "user": user_id},
            )
        else:
            c.execute(
                text("UPDATE memberships SET role=:role WHERE team_id=:team AND user_id=:user"),
                {"role": role, "team": team_id, "user": user_id},
            )


def recover(target, username, recovery_code, new_password):
    limit_login(target, username)
    encoded = _password(new_password)
    new_code = secrets.token_urlsafe(24)
    with transaction(target, "accounts") as c:
        row = (
            c.execute(
                text("SELECT * FROM users WHERE username=:name"), {"name": username.strip().lower()}
            )
            .mappings()
            .first()
        )
        if not row or not secrets.compare_digest(row["recovery_hash"], _digest(recovery_code)):
            raise ValueError("Username or recovery code is incorrect.")
        c.execute(
            text(
                "UPDATE users SET password_hash=:password, recovery_hash=:recovery, "
                "session_version=session_version+1 WHERE id=:id"
            ),
            {"password": encoded, "recovery": _digest(new_code), "id": row["id"]},
        )
    return new_code


def change_password(target, account, old_password, new_password):
    limit_login(target, account.username)
    with transaction(target, "accounts") as c:
        require_account(c, account)
        stored = c.execute(
            text("SELECT password_hash FROM users WHERE id=:id"), {"id": account.id}
        ).scalar_one()
        if not verify_password(old_password, stored):
            raise ValueError("Current password is incorrect.")
        c.execute(
            text(
                "UPDATE users SET password_hash=:password, session_version=session_version+1 WHERE id=:id"
            ),
            {"password": _password(new_password), "id": account.id},
        )
