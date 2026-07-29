"""Streamlit entry point for Swim Tracker."""

from __future__ import annotations

import os
from pathlib import Path

from openai import OpenAIError
import pandas as pd
import streamlit as st

from swim_tracker.ai_search import interpret_search
from swim_tracker.database import (
    filter_options,
    initialize_database,
    rebuild_database,
    replace_source_results,
    result_count,
    schema_is_current,
    search_results,
    source_summary,
)
from swim_tracker.parser import parse_cl2_file, parse_cl2_text


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_FILE = (
    APP_ROOT / "Meet Results-2024 TAC TITANS Jingle Bells Meet-20Dec2024-001.cl2"
)


def database_path() -> Path:
    return Path(
        os.environ.get("SWIMTRACKER_DB_PATH", APP_ROOT / "swim_data.db")
    ).expanduser()


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


def prepare_database() -> None:
    """Upgrade legacy data once and seed an empty database from bundled data."""
    path = database_path()

    if not schema_is_current(path):
        if not DEFAULT_DATA_FILE.exists():
            raise FileNotFoundError(
                "The database needs rebuilding, but the bundled CL2 file is missing."
            )
        rebuild_database(path, parse_cl2_file(DEFAULT_DATA_FILE))
        return

    initialize_database(path)
    if result_count(path) == 0 and DEFAULT_DATA_FILE.exists():
        replace_source_results(path, parse_cl2_file(DEFAULT_DATA_FILE))


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

    options = filter_options(database_path())
    manual_tab, ai_tab = st.tabs(["Filters", "Ask AI"])

    with manual_tab:
        with st.form("manual_search"):
            name = st.text_input(
                "Swimmer name",
                placeholder="For example: Natalie Xu",
            )
            col1, col2, col3 = st.columns(3)
            with col1:
                group = st.selectbox(
                    "Age group", ["All groups", *options["groups"]]
                )
            with col2:
                event = st.selectbox("Event", ["All events", *options["events"]])
            with col3:
                sort_label = st.selectbox(
                    "Sort by", ["Swimmer and event", "Fastest time"]
                )
            limit = st.slider("Maximum results", 25, 500, 200, step=25)
            submitted = st.form_submit_button(
                "Search results", type="primary", width="stretch"
            )

        if submitted:
            results = search_results(
                database_path(),
                name=name,
                group_label=None if group == "All groups" else group,
                event=None if event == "All events" else event,
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
                placeholder="For example: Show Natalie Xu's fastest 100-yard times",
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
            "Distance": (
                f"{filters.distance_yards} yards"
                if filters.distance_yards is not None
                else None
            ),
            "Stroke": filters.stroke,
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
            database_path(),
            name=filters.swimmer_name,
            group_label=filters.group_label,
            distance_yards=filters.distance_yards,
            stroke=filters.stroke,
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

    summary = source_summary(database_path())
    if summary.empty:
        st.info("No meet data has been imported.")
    else:
        st.dataframe(summary, width="stretch", hide_index=True)

    uploaded_file = st.file_uploader("Choose a CL2 file", type=["cl2"])
    if uploaded_file is not None:
        text = uploaded_file.getvalue().decode("utf-8", errors="replace")
        parsed = parse_cl2_text(text, source_file=uploaded_file.name)
        st.write(f"Found **{len(parsed):,} completed individual results**.")
        if parsed and st.button(
            "Import this meet", type="primary", width="stretch"
        ):
            replace_source_results(database_path(), parsed)
            st.success(f"Imported {len(parsed):,} results from {uploaded_file.name}.")
            st.rerun()

    st.divider()
    if st.button("Reload bundled sample meet"):
        parsed = parse_cl2_file(DEFAULT_DATA_FILE)
        replace_source_results(database_path(), parsed)
        st.success(f"Reloaded {len(parsed):,} completed results.")
        st.rerun()


def resources_page() -> None:
    st.title("Resources")
    st.write(
        "Use the search page for meet results. The embedded SwimCloud profile "
        "below is retained from the original project."
    )
    st.components.v1.html(
        '<iframe src="https://www.swimcloud.com/swimmer/1050995/iframe/'
        '?splashes_type=fastest" width="100%" height="600" '
        'frameborder="0" title="SwimCloud swimmer profile"></iframe>',
        height=620,
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
        st.metric("Completed results", f"{result_count(database_path()):,}")
        st.caption(
            "AI search ready"
            if _secret("OPENAI_API_KEY")
            else "AI search not configured"
        )

    navigation = st.navigation(
        [
            st.Page(search_page, title="Search", icon="🔎", default=True),
            st.Page(data_page, title="Meet data", icon="📥"),
            st.Page(resources_page, title="Resources", icon="🔗"),
        ]
    )
    navigation.run()


if __name__ == "__main__":
    main()
