"""Streamlit entry point for Swim Tracker."""

from __future__ import annotations

from collections.abc import MutableMapping
from datetime import date
import hmac
import os
from pathlib import Path

from openai import OpenAIError
import pandas as pd
from sqlalchemy.exc import SQLAlchemyError
import streamlit as st

from swim_tracker.ai_search import interpret_search
from swim_tracker.database import (
    delete_source_results,
    filter_options,
    get_meta,
    initialize_database,
    rebuild_database,
    replace_source_results,
    result_count,
    schema_is_current,
    search_results,
    set_meta,
    source_summary,
)
from swim_tracker.parser import parse_cl2_file, parse_cl2_text


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_FILE = (
    APP_ROOT / "Meet Results-2024 TAC TITANS Jingle Bells Meet-20Dec2024-001.cl2"
)


def database_target() -> str:
    """Pick the database this app instance talks to.

    ``SWIMTRACKER_DB_PATH`` (used by tests) wins, then a hosted
    ``DATABASE_URL`` such as a Neon Postgres connection string, then the
    bundled local SQLite file.
    """
    override = os.environ.get("SWIMTRACKER_DB_PATH")
    if override:
        return override
    url = _secret("DATABASE_URL")
    if url:
        return url
    return str(APP_ROOT / "swim_data.db")


def _secret(name: str, default: str | None = None) -> str | None:
    """Read server-side configuration without ever displaying its value."""
    environment_value = os.environ.get(name)
    if environment_value:
        return environment_value

    try:
        value = st.secrets.get(name)
    except Exception:
        value = None
    return str(value) if value else default


SEEDED_META_KEY = "seeded_bundled_meet"
ADMIN_SESSION_KEY = "admin_unlocked"


def verify_admin_password(supplied: str, expected: str) -> bool:
    return hmac.compare_digest(supplied.encode(), expected.encode())


def admin_unlocked(session: MutableMapping | None = None) -> bool:
    """Whether this session may import or remove meet data.

    With no ADMIN_PASSWORD configured (local development), writes stay open.
    """
    expected = _secret("ADMIN_PASSWORD")
    if not expected:
        return True
    if session is None:
        session = st.session_state
    return bool(session.get(ADMIN_SESSION_KEY))


def _render_admin_login() -> None:
    st.info(
        "Importing or removing meet data on this deployment requires the "
        "admin password. Searching does not."
    )
    with st.form("admin_login"):
        supplied = st.text_input("Admin password", type="password")
        submitted = st.form_submit_button("Unlock meet data management")
    if submitted:
        expected = _secret("ADMIN_PASSWORD") or ""
        if verify_admin_password(supplied, expected):
            st.session_state[ADMIN_SESSION_KEY] = True
            st.rerun()
        else:
            st.error("That password is not correct.")


def prepare_database() -> None:
    """Upgrade legacy data once and seed a brand-new database from bundled data.

    Seeding happens at most once per database, so removing every imported meet
    leaves the database empty instead of silently restoring the sample data.
    """
    path = database_target()

    if not schema_is_current(path):
        if not DEFAULT_DATA_FILE.exists():
            raise FileNotFoundError(
                "The database needs rebuilding, but the bundled CL2 file is missing."
            )
        rebuild_database(path, parse_cl2_file(DEFAULT_DATA_FILE))
        set_meta(path, SEEDED_META_KEY, "1")
        return

    initialize_database(path)
    already_seeded = get_meta(path, SEEDED_META_KEY) == "1"
    if (
        result_count(path) == 0
        and not already_seeded
        and DEFAULT_DATA_FILE.exists()
    ):
        replace_source_results(path, parse_cl2_file(DEFAULT_DATA_FILE))
    if not already_seeded and result_count(path) > 0:
        set_meta(path, SEEDED_META_KEY, "1")


def _show_results(results: pd.DataFrame) -> None:
    if results.empty:
        st.info("No completed results matched those filters.")
        return

    st.caption(f"Showing {len(results):,} result{'s' if len(results) != 1 else ''}.")
    st.dataframe(
        results.drop(columns=["Time (Seconds)"]),
        width="stretch",
        hide_index=True,
    )

    st.download_button(
        "Download these results as CSV",
        results.drop(columns=["Time (Seconds)"]).to_csv(index=False),
        file_name="swim-results.csv",
        mime="text/csv",
    )

    if len(results) <= 100:
        chart_data = results.copy()
        chart_data["Swimmer · Event"] = (
            chart_data["Name"] + " · " + chart_data["Event"]
        )
        st.bar_chart(
            chart_data,
            x="Swimmer · Event",
            y="Time (Seconds)",
            horizontal=True,
        )
    else:
        st.caption("Narrow the search to 100 results or fewer to display a chart.")


def search_page() -> None:
    st.title("Swim Tracker")
    st.write("Search completed individual results from imported meet files.")

    options = filter_options(database_target())
    manual_tab, ai_tab = st.tabs(["Filters", "Ask AI"])

    with manual_tab:
        date_bounds = options["date_range"]
        with st.form("manual_search"):
            name = st.text_input(
                "Swimmer name",
                placeholder="For example: John Doe",
            )
            col1, col2, col3 = st.columns(3)
            with col1:
                group = st.selectbox(
                    "Age group", ["All groups", *options["groups"]]
                )
            with col2:
                event = st.selectbox("Event", ["All events", *options["events"]])
            with col3:
                course = st.selectbox(
                    "Course", ["All courses", *options["courses"]]
                )
            col4, col5 = st.columns([1, 2])
            with col4:
                sort_label = st.selectbox(
                    "Sort by", ["Swimmer and event", "Fastest time"]
                )
            with col5:
                selected_dates = (
                    st.date_input(
                        "Meet dates",
                        value=(
                            date.fromisoformat(date_bounds[0]),
                            date.fromisoformat(date_bounds[1]),
                        ),
                    )
                    if date_bounds
                    else None
                )
            limit = st.slider("Maximum results", 25, 500, 200, step=25)
            submitted = st.form_submit_button(
                "Search results", type="primary", width="stretch"
            )

        if submitted:
            date_from = date_to = None
            if isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 2:
                date_from = selected_dates[0].isoformat()
                date_to = selected_dates[1].isoformat()
            results = search_results(
                database_target(),
                name=name,
                group_label=None if group == "All groups" else group,
                event=None if event == "All events" else event,
                course=None if course == "All courses" else course,
                date_from=date_from,
                date_to=date_to,
                sort_order=(
                    "fastest" if sort_label == "Fastest time" else "name"
                ),
                limit=limit,
            )
            _show_results(results)
        else:
            st.caption("Choose filters and select **Search results**.")

    with ai_tab:
        api_key = _secret("OPENAI_API_KEY")
        if not api_key:
            st.info(
                "AI search is optional. Add `OPENAI_API_KEY` to your environment "
                "or `.streamlit/secrets.toml` to enable it. Filter search works "
                "without an API key."
            )
            return

        with st.form("ai_search"):
            query = st.text_input(
                "Ask about swimmer performance",
                placeholder=(
                    "For example: Fastest 100 free times for girls 11-12 "
                    "in December 2024"
                ),
            )
            ai_submitted = st.form_submit_button(
                "Interpret and search", type="primary", width="stretch"
            )

        if not ai_submitted:
            return
        if not query.strip():
            st.warning("Enter a question first.")
            return

        model = _secret("OPENAI_MODEL", "gpt-5.6-luna")
        try:
            with st.spinner("Interpreting your search…"):
                filters = interpret_search(
                    query,
                    api_key=api_key,
                    model=model or "gpt-5.6-luna",
                    available_groups=options["groups"],
                )
        except (OpenAIError, ValueError) as exc:
            st.error(
                "AI search could not interpret that request. Check the API key, "
                "billing, network connection, and model setting, then try again."
            )
            st.caption(f"Technical detail: {type(exc).__name__}")
            return

        selected_filters = {
            "Swimmer": filters.swimmer_name,
            "Group": filters.group_label,
            "Distance": filters.distance,
            "Stroke": filters.stroke,
            "Course": filters.course,
            "From": filters.date_from,
            "To": filters.date_to,
            "Sort": filters.sort_order,
        }
        st.caption(
            "Interpreted filters: "
            + ", ".join(
                f"{key}: {value}"
                for key, value in selected_filters.items()
                if value is not None
            )
        )
        results = search_results(
            database_target(),
            name=filters.swimmer_name,
            group_label=filters.group_label,
            distance_yards=filters.distance,
            stroke=filters.stroke,
            course=filters.course,
            date_from=filters.date_from,
            date_to=filters.date_to,
            sort_order=filters.sort_order,
            limit=filters.max_results,
        )
        _show_results(results)


def data_page() -> None:
    st.title("Meet Data")
    st.write(
        "Import a Hy-Tek/Team Manager `.cl2` file. Re-importing a file with the "
        "same name replaces that file's previous rows instead of creating duplicates."
    )

    summary = source_summary(database_target())
    if summary.empty:
        st.info("No meet data has been imported.")
    else:
        st.dataframe(summary, width="stretch", hide_index=True)

    if not admin_unlocked():
        _render_admin_login()
        return

    if not summary.empty:
        with st.expander("Remove an imported meet"):
            source_to_remove = st.selectbox(
                "Meet file to remove", summary["Source file"]
            )
            st.caption(
                "This permanently removes every result imported from the "
                "selected file."
            )
            if st.button("Remove this meet's results"):
                removed = delete_source_results(
                    database_target(), source_to_remove
                )
                st.success(
                    f"Removed {removed:,} results from {source_to_remove}."
                )
                st.rerun()

    uploaded_file = st.file_uploader("Choose a CL2 file", type=["cl2"])
    if uploaded_file is not None:
        text = uploaded_file.getvalue().decode("utf-8", errors="replace")
        parsed = parse_cl2_text(text, source_file=uploaded_file.name)
        st.write(f"Found **{len(parsed):,} completed individual results**.")
        if parsed and st.button(
            "Import this meet", type="primary", width="stretch"
        ):
            replace_source_results(database_target(), parsed)
            st.success(f"Imported {len(parsed):,} results from {uploaded_file.name}.")
            st.rerun()

    st.divider()
    if st.button("Reload bundled sample meet"):
        parsed = parse_cl2_file(DEFAULT_DATA_FILE)
        replace_source_results(database_target(), parsed)
        st.success(f"Reloaded {len(parsed):,} completed results.")
        st.rerun()


def about_page() -> None:
    st.title("About Swim Tracker")
    st.markdown(
        """
Swim Tracker imports Hy-Tek/Team Manager `.cl2` meet-result files and makes
completed individual results searchable.

**Search** supports filtering by swimmer name, age/gender group, event,
course (SCY, SCM, or LCM), and meet date, with results sortable by swimmer
or by fastest time. Matching results can be downloaded as CSV.

**Ask AI** turns a natural-language question, such as *"fastest 100 free
times for girls 11-12 in December 2024"*, into the same validated filters.
The model returns structured filter values only; those values are bound as
parameters into fixed SQL queries, so model output is never executed as SQL
and swimmer-name input cannot be either.

**Meet data** imports additional `.cl2` files. Re-importing a file with the
same name replaces its earlier rows, and any imported meet can be removed
again without affecting the others.

The bundled sample data is one publicly published meet-results file. All
data stays in a local SQLite database on the server; the only external call
is the optional OpenAI request that interprets **Ask AI** questions, which
sends the question text and the list of age groups, never the results
themselves.

Source code and documentation:
[github.com/Brandon-Xu1/Swim-Tracker](https://github.com/Brandon-Xu1/Swim-Tracker)
        """
    )


def main() -> None:
    st.set_page_config(
        page_title="Swim Tracker",
        page_icon="🏊",
        layout="wide",
    )

    try:
        prepare_database()
    except (OSError, ValueError) as exc:
        st.error(f"Swim Tracker could not prepare its database: {exc}")
        st.stop()

    with st.sidebar:
        st.header("Swim Tracker")
        st.metric("Completed results", f"{result_count(database_target()):,}")
        st.caption(
            "AI search ready"
            if _secret("OPENAI_API_KEY")
            else "AI search not configured"
        )

    navigation = st.navigation(
        [
            st.Page(search_page, title="Search", icon="🔎", default=True),
            st.Page(data_page, title="Meet data", icon="📥"),
            st.Page(about_page, title="About", icon="ℹ️"),
        ]
    )
    navigation.run()


if __name__ == "__main__":
    main()
