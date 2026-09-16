"""Session identity and configuration shared by all pages."""

import os
from pathlib import Path

import streamlit as st

from .. import accounts

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "Meet Results-2024 TAC TITANS Jingle Bells Meet-20Dec2024-001.cl2"


def secret(name, default=None):
    value = os.environ.get(name)
    if value:
        return value
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None
    return str(value) if value else default


def target():
    return (
        os.environ.get("SWIMTRACKER_DB_PATH")
        or secret("DATABASE_URL")
        or str(ROOT / "swim_data.db")
    )


def account():
    saved = st.session_state.get("account")
    return accounts.current(target(), saved["id"], saved["version"]) if saved else None


def team():
    user = account()
    if user is None:
        return None
    choices = accounts.teams_for(target(), user)
    return next((row for row in choices if row["id"] == st.session_state.get("active_team")), None)


def visible():
    selected = team()
    return [0, selected["id"]] if selected else [0]


def can_edit():
    selected = team()
    return bool(selected and selected["role"] in ("coach", "owner"))


def reset_session(user=None):
    # Clear result filters and widget state at every identity transition.
    for key in list(st.session_state):
        del st.session_state[key]
    if user:
        st.session_state["account"] = {"id": user.id, "version": user.version}


def csv_bytes(frame):
    safe = frame.copy()
    for col in safe.select_dtypes(include=["object", "string"]).columns:
        safe[col] = safe[col].map(
            lambda value: (
                "'" + value
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                else value
            )
        )
    return safe.to_csv(index=False).encode()
