"""Swim Tracker application shell; pages and services live in swim_tracker."""

import hashlib
import hmac
import logging
from collections.abc import MutableMapping

import streamlit as st
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from swim_tracker import database as db
from swim_tracker.imports import (
    import_meet,
    inspect_upload,
    read_upload,
    remove_meet,
)
from swim_tracker.profiles import link_rows
from swim_tracker.rate_limit import RateLimitedError
from swim_tracker.storage import limit_login, transaction
from swim_tracker.ui import account_page, profile_page
from swim_tracker.ui import context as ctx
from swim_tracker.ui.search_page import options as _cached_filter_options
from swim_tracker.ui.search_page import page as search_page

APP_ROOT = ctx.ROOT
DEFAULT_DATA_FILE = ctx.SAMPLE
ADMIN_SESSION_KEY = "admin_unlocked"
SEEDED_META_KEY = "seeded_bundled_meet"
database_target = ctx.target
_secret = ctx.secret
visible_team_ids = ctx.visible


def verify_admin_password(supplied, expected):
    return hmac.compare_digest(supplied.encode(), expected.encode())


def admin_unlocked(session: MutableMapping | None = None):
    expected = ctx.secret("ADMIN_PASSWORD")
    if not expected:
        return ctx.secret("ALLOW_PUBLIC_WRITES", "false").lower() == "true"
    session = st.session_state if session is None else session
    return session.get(ADMIN_SESSION_KEY) == hashlib.sha256(expected.encode()).hexdigest()


def prepare_database(target=None):
    """Initialize additive tables and seed once, without destructive rebuilds."""
    target = target or database_target()
    db.initialize_database(target)
    with transaction(target, "startup") as c:
        if c.execute(
            text("SELECT value FROM app_meta WHERE key=:key"), {"key": SEEDED_META_KEY}
        ).scalar():
            return
        count = c.execute(text("SELECT COUNT(*) FROM results")).scalar_one()
        if count == 0 and DEFAULT_DATA_FILE.exists():
            from dataclasses import asdict

            raw = DEFAULT_DATA_FILE.read_bytes()
            report = inspect_upload(DEFAULT_DATA_FILE.name, raw)
            if not report.results or report.issues:
                raise ValueError("Bundled sample failed validation; database was preserved.")
            c.execute(
                db.results_table.insert(), [{"team_id": 0, **asdict(row)} for row in report.results]
            )
            c.execute(
                db.raw_files_table.insert().values(
                    team_id=0,
                    filename=DEFAULT_DATA_FILE.name,
                    content=raw,
                    uploaded_at=db._utc_now_iso(),
                )
            )
            link_rows(
                c, 0, c.execute(text("SELECT * FROM results WHERE team_id=0")).mappings().all()
            )
        c.execute(
            text(
                "INSERT INTO app_meta (key, value) VALUES (:key, :value) ON CONFLICT (key) DO NOTHING"
            ),
            {"key": SEEDED_META_KEY, "value": "1"},
        )


@st.cache_resource(show_spinner="Preparing your workspace…")
def _database_prepared(target):
    prepare_database(target)
    return True


def _clear_cached_data():
    _cached_filter_options.clear()


def _render_admin_login():
    if not ctx.secret("ADMIN_PASSWORD"):
        st.info(
            "Public sample data is read-only. Create a team on the Account page to import your own meets."
        )
        return
    with st.form("admin_login"):
        supplied = st.text_input("Admin password", type="password", max_chars=256)
        submitted = st.form_submit_button("Unlock meet data management")
    if submitted:
        try:
            limit_login(database_target(), "deployment-admin")
            expected = ctx.secret("ADMIN_PASSWORD")
            if verify_admin_password(supplied, expected):
                st.session_state[ADMIN_SESSION_KEY] = hashlib.sha256(expected.encode()).hexdigest()
                st.rerun()
            st.error("That password is not correct.")
        except RateLimitedError as exc:
            st.warning(f"Try again in {int(exc.retry_after_seconds) + 1} seconds.")


def data_page():
    st.title("Meet data")
    selected = ctx.team()
    team_id = selected["id"] if selected else 0
    editable = ctx.can_edit() if selected else admin_unlocked()
    st.write(
        f"Your workspace: **{selected['name']}**"
        if selected
        else "Explore the shared public sample."
    )
    if selected:
        st.caption(
            "Team uploads are visible only to members. Replacing a meet updates its results and keeps matched swimmer profiles."
        )
    summary = db.source_summary(database_target(), team_id=team_id)
    st.dataframe(summary, hide_index=True, width="stretch")
    if not summary.empty:
        with st.expander("Download an original meet file"):
            source = st.selectbox("Original file", summary["Source file"])
            raw = db.get_raw_file(database_target(), team_id, source)
            if raw:
                st.download_button(
                    "Download original", raw, file_name=source, mime="application/octet-stream"
                )
    if DEFAULT_DATA_FILE.exists():
        st.download_button(
            "Download a sample .cl2 file to try importing",
            DEFAULT_DATA_FILE.read_bytes(),
            DEFAULT_DATA_FILE.name,
        )
    if not editable:
        if selected:
            st.info(
                "Your viewer role can explore results. Ask a team owner for coach access to import or remove meets."
            )
        else:
            _render_admin_login()
        return
    st.subheader("Import meets")
    st.caption(
        "CL2 files, or ZIPs containing CL2 files · 8 MB per upload · originals are preserved"
    )
    uploaded = st.file_uploader(
        "Choose meet files", type=["cl2", "zip"], accept_multiple_files=True
    )
    for upload_index, upload in enumerate(uploaded or []):
        try:
            files = read_upload(upload.name, upload.getvalue())
        except ValueError as exc:
            st.error(f"{upload.name}: {exc}")
            continue
        for file_index, (filename, raw) in enumerate(files):
            key = f"{team_id}_{upload_index}_{file_index}_{hashlib.sha256(raw).hexdigest()[:12]}"
            try:
                report = inspect_upload(filename, raw)
                with st.container(border=True):
                    st.subheader(filename)
                    a, b, c = st.columns(3)
                    a.metric("Ready to import", f"{len(report.results):,}")
                    b.metric("Without a completed time", report.excluded)
                    c.metric("Needs review", len(report.issues))
                    if report.issues:
                        from dataclasses import asdict

                        st.warning(
                            "Some individual records could not be parsed. Review their line numbers before importing."
                        )
                        st.dataframe([asdict(issue) for issue in report.issues], hide_index=True)
                    exists = not summary.empty and filename in set(summary["Source file"])
                    with st.form("import_" + key):
                        allow_partial = (
                            st.checkbox("Import valid records and skip the errors shown above")
                            if report.issues
                            else False
                        )
                        replace = (
                            st.checkbox("Replace the existing meet with this filename")
                            if exists
                            else False
                        )
                        submitted = st.form_submit_button(
                            "Import this meet", type="primary", disabled=not report.results
                        )
                    if submitted:
                        outcome = import_meet(
                            database_target(),
                            filename,
                            raw,
                            team_id=team_id,
                            account=ctx.account(),
                            public_admin=team_id == 0 and admin_unlocked(),
                            allow_partial=allow_partial,
                            replace_existing=replace,
                        )
                        message = (
                            f"Already imported as {outcome.filename}; no duplicate results added."
                            if outcome.duplicate
                            else f"Imported {outcome.count:,} results from {outcome.filename}."
                        )
                        st.session_state["flash"] = message
                        _clear_cached_data()
                        st.rerun()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
    if not summary.empty:
        with st.expander("Remove an imported meet"):
            with st.form("remove_meet"):
                source = st.selectbox("Meet to remove", summary["Source file"])
                confirm = st.checkbox("Remove this meet’s results and original file")
                submitted = st.form_submit_button("Remove meet")
            if submitted:
                if not confirm:
                    st.warning("Confirm removal first.")
                else:
                    remove_meet(
                        database_target(),
                        source,
                        team_id=team_id,
                        account=ctx.account(),
                        public_admin=team_id == 0 and admin_unlocked(),
                    )
                    _clear_cached_data()
                    st.session_state["flash"] = f"Removed {source}."
                    st.rerun()
    if team_id == 0 and DEFAULT_DATA_FILE.exists():
        if st.button("Reload bundled sample meet"):
            import_meet(
                database_target(),
                DEFAULT_DATA_FILE.name,
                DEFAULT_DATA_FILE.read_bytes(),
                public_admin=admin_unlocked(),
                replace_existing=True,
            )
            _clear_cached_data()
            st.rerun()


def about_page():
    st.title("About Swim Tracker")
    st.write("A workspace for turning meet-result files into useful swimmer histories.")
    st.markdown("""
- **Search** completed individual swims by event, course, date and age group.
- **Swimmers** combines imported history into profiles with best times, progress and goals.
- **Meet data** previews imports, reports errors and prevents duplicate files.
- **Account** gives each person their own login, with owner, coach and viewer roles.

Profiles cover uploaded meets only. They do not represent a complete national or lifetime history.
Pool courses are kept separate. Blank-time records and relays are outside this app’s completed-individual-results scope.

AI search translates a question into editable search filters. It cannot predict performance or rank improvement.
Only the question and available age-group labels are sent to the AI provider; result tables are not sent.

[Source code and setup instructions](https://github.com/Brandon-Xu1/Swim-Tracker)
""")


def main():
    st.set_page_config(page_title="Swim Tracker", page_icon="🏊", layout="wide")
    try:
        _database_prepared(database_target())
    except (OSError, ValueError, SQLAlchemyError) as exc:
        logging.getLogger(__name__).error("Workspace startup failed: %s", type(exc).__name__)
        st.error(
            "The workspace could not be opened. Existing data has been preserved. Check database configuration and server logs."
        )
        st.stop()
    if "next_team" in st.session_state:
        st.session_state["active_team"] = st.session_state.pop("next_team")
    if st.session_state.get("account") and ctx.account() is None:
        ctx.reset_session()
        st.info("Your session expired. Sign in again on Account.")
    with st.sidebar:
        st.header("Swim Tracker")
        st.caption("YOUR SEASON, IN PERSPECTIVE")
        account_page.sidebar()
        st.divider()
        st.metric("Completed results", f"{db.result_count(database_target(), ctx.visible()):,}")
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))
    navigation = st.navigation(
        [
            st.Page(search_page, title="Search", icon="🔎", default=True),
            st.Page(profile_page.page, title="Swimmers", icon="🏊", url_path="swimmers"),
            st.Page(data_page, title="Meet data", icon="📥"),
            st.Page(account_page.page, title="Account", icon="👤", url_path="account"),
            st.Page(about_page, title="About", icon="ℹ️"),
        ]
    )
    try:
        navigation.run()
    except PermissionError as exc:
        st.error(str(exc))
    except SQLAlchemyError:
        st.error(
            "The database is temporarily unavailable. Please try again. No partial import was saved."
        )


if __name__ == "__main__":
    main()
