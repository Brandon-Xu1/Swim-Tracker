"""Team accounts: scrypt password hashing, registration, and sign-in.

Hashes use Python's standard-library scrypt with a per-password random
salt, stored as ``scrypt$<n>$<r>$<p>$<salt-hex>$<digest-hex>`` so the
parameters can be strengthened later without invalidating old hashes.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import secrets

from . import database


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
DIGEST_LENGTH = 32

MIN_TEAM_NAME_LENGTH = 3
MAX_TEAM_NAME_LENGTH = 40
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=DIGEST_LENGTH,
    )
    return (
        f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}"
        f"${salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, bytes.fromhex(digest_hex))


def _normalized_team_name(name: str) -> str:
    normalized = " ".join(name.split())
    if not (
        MIN_TEAM_NAME_LENGTH <= len(normalized) <= MAX_TEAM_NAME_LENGTH
    ):
        raise ValueError(
            f"Team names must be {MIN_TEAM_NAME_LENGTH} to "
            f"{MAX_TEAM_NAME_LENGTH} characters."
        )
    return normalized


def register_team(target: str | Path, name: str, password: str) -> int:
    """Create a team account and return its id."""
    normalized = _normalized_team_name(name)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    return database.create_team(target, normalized, hash_password(password))


def authenticate_team(
    target: str | Path, name: str, password: str
) -> tuple[int, str] | None:
    """Return (team_id, display_name) for valid credentials, else None."""
    row = database.get_team(target, name)
    if row is None:
        return None
    if not verify_password(password, row.password_hash):
        return None
    return int(row.id), str(row.name)
