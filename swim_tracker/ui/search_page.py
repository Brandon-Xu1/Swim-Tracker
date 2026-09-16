import time
from datetime import date

import altair as alt
import streamlit as st
from openai import OpenAIError

from .. import database as db
from ..ai_search import AISearchFilters, interpret_search
from ..rate_limit import RateLimitedError, SlidingWindowLimit
from ..storage import acquire_quotas
from . import context as ctx


@st.cache_data(ttl=300, show_spinner=False)
def options(target, scope):
    return db.filter_options(target, list(scope))


@st.cache_data(ttl=86400, max_entries=512, show_spinner=False)
def interpret_cached(target, user_id, query, model, groups, today, daily_limit, _api_key):
    acquire_quotas(
        target,
        [
            (f"ai:user:{user_id}", [SlidingWindowLimit(5, 60), SlidingWindowLimit(30, 3600)]),
            ("ai:global", [SlidingWindowLimit(30, 60), SlidingWindowLimit(daily_limit, 86400)]),
        ],
    )
    return interpret_search(
        query,
        api_key=_api_key,
        model=model,
        available_groups=list(groups),
        reference_date=date.fromisoformat(today),
    )


def show_results(results, *, key="manual"):
    if results.empty:
        st.info("No completed results matched those filters.")
        return
    total = results.attrs.get("total", len(results))
    st.caption(f"{total:,} matching results · showing {len(results):,} on this page")
    visible = results.drop(columns=["Time (Seconds)"])
    st.dataframe(visible, hide_index=True, width="stretch")
    st.download_button(
        "Download this page as CSV",
        ctx.csv_bytes(visible),
        "swim-results.csv",
        "text/csv",
        key=f"csv_{key}",
    )
    combinations = results[["Event", "Course"]].drop_duplicates()
    if len(combinations) == 1:
        chart = (
            alt.Chart(results)
            .mark_circle(size=75)
            .encode(
                x=alt.X("Time (Seconds):Q", scale=alt.Scale(zero=False)),
                y=alt.Y("Name:N", sort=alt.SortField(field="Time (Seconds)", order="ascending")),
                tooltip=["Name", "Event", "Course", "Time", "Date", "Meet"],
                color=alt.value("#17a398"),
            )
            .properties(height=min(600, max(160, results.Name.nunique() * 24)))
        )
        st.altair_chart(chart, width="stretch")
        st.caption("One dot per swim on this page. Lower time is faster.")
    else:
        st.caption("Select one event and course to compare swim times on a chart.")


def page():
    st.title("Swim Tracker")
    st.write("Find a result. Follow a swimmer. See the progress.")
    scope = tuple(ctx.visible())
    if st.session_state.get("search_scope", scope) != scope:
        for key in ("manual_filters", "manual_offset", "ai_filters", "ai_results_filters"):
            st.session_state.pop(key, None)
    st.session_state["search_scope"] = scope
    saved = st.session_state.get("manual_filters", {})
    opts = options(ctx.target(), scope)
    count = db.result_count(ctx.target(), list(scope))
    a, b, c = st.columns(3)
    a.metric("Recorded swims", f"{count:,}")
    b.metric("Events", len(opts["events"]))
    c.metric("Pool courses", len(opts["courses"]))
    manual, ai = st.tabs(["Filters", "Ask AI"])
    with manual:
        with st.form(f"manual_search_{scope}"):
            name = st.text_input(
                "Swimmer name",
                value=saved.get("name", ""),
                placeholder="For example: Pierce Arora",
                max_chars=100,
            )
            a, b, c = st.columns(3)
            groups = ["All groups", *opts["groups"]]
            events = ["All events", *opts["events"]]
            courses = ["All courses", *opts["courses"]]
            group = a.selectbox(
                "Age group",
                groups,
                index=groups.index(saved["group_label"])
                if saved.get("group_label") in groups
                else 0,
            )
            event = b.selectbox(
                "Event",
                events,
                index=events.index(saved["event"]) if saved.get("event") in events else 0,
            )
            course = c.selectbox(
                "Course",
                courses,
                index=courses.index(saved["course"]) if saved.get("course") in courses else 0,
            )
            a, b = st.columns([1, 2])
            sort = a.selectbox(
                "Sort by",
                ["Swimmer and event", "Fastest time"],
                index=1 if saved.get("sort_order") == "fastest" else 0,
            )
            saved_dates = (saved.get("date_from"), saved.get("date_to"))
            date_range = saved_dates if all(saved_dates) else opts["date_range"]
            dates = (
                b.date_input(
                    "Meet dates",
                    value=tuple(date.fromisoformat(value) for value in date_range),
                )
                if date_range
                else None
            )
            size = st.select_slider(
                "Results per page", options=[25, 50, 100, 200], value=saved.get("limit", 50)
            )
            submitted = st.form_submit_button("Search results", type="primary", width="stretch")
        if submitted:
            if dates is not None and len(dates) != 2:
                st.warning("Select both a start and end date.")
            else:
                st.session_state["manual_filters"] = dict(
                    name=name,
                    group_label=None if group == "All groups" else group,
                    event=None if event == "All events" else event,
                    course=None if course == "All courses" else course,
                    sort_order="fastest" if sort == "Fastest time" else "name",
                    limit=size,
                    date_from=dates[0].isoformat() if dates else None,
                    date_to=dates[1].isoformat() if dates else None,
                )
                st.session_state["manual_offset"] = 0
        filters = st.session_state.get("manual_filters")
        if filters:
            offset = st.session_state.get("manual_offset", 0)
            started = time.perf_counter()
            results = db.search_results(
                ctx.target(), **filters, offset=offset, team_ids=list(scope)
            )
            query_ms = (time.perf_counter() - started) * 1000
            if results.empty and offset:
                st.session_state["manual_offset"] = 0
                st.rerun()
            show_results(results)
            total = results.attrs.get("total", 0)
            a, b, c = st.columns([1, 2, 1])
            if a.button("Previous page", disabled=offset == 0):
                st.session_state["manual_offset"] = max(0, offset - filters["limit"])
                st.rerun()
            b.caption(f"Page {offset // filters['limit'] + 1} · query {query_ms:.0f} ms")
            if c.button("Next page", disabled=offset + len(results) >= total):
                st.session_state["manual_offset"] = offset + filters["limit"]
                st.rerun()
        else:
            st.caption(
                "Choose filters and select Search results. Explore Swimmers for best times and race history."
            )
    with ai:
        _ai(opts, scope)


def _ai(opts, scope):
    api_key = ctx.secret("OPENAI_API_KEY")
    if not api_key:
        st.info(
            "AI search is not enabled on this deployment. You can search every result using Filters."
        )
        return
    user = ctx.account()
    if not user:
        st.info("Sign in on the Account page to use AI search.")
        return
    st.caption(
        "Describe a search, review the interpreted filters, then run it. Progress and best-time analysis are available on Swimmers."
    )
    with st.form(f"ai_search_{scope}"):
        query = st.text_input(
            "Describe your search",
            placeholder="Fastest 100 free for girls 11-12 in December 2024",
            max_chars=1000,
        )
        submitted = st.form_submit_button("Interpret question", type="primary")
    if submitted:
        if not query.strip():
            st.warning("Enter a question first.")
            return
        st.session_state.pop("ai_filters", None)
        st.session_state.pop("ai_results_filters", None)
        try:
            with st.spinner("Interpreting your search…"):
                filters = interpret_cached(
                    ctx.target(),
                    user.id,
                    " ".join(query.split()),
                    ctx.secret("OPENAI_MODEL", "gpt-5.6-luna"),
                    tuple(opts["groups"]),
                    date.today().isoformat(),
                    max(1, int(ctx.secret("AI_DAILY_CALL_LIMIT", "200"))),
                    api_key,
                )
            if filters.intent == "unsupported":
                st.warning(
                    filters.explanation
                    or "This question needs analysis beyond the available search filters."
                )
            else:
                st.session_state["ai_filters"] = filters.model_dump()
                st.session_state["ai_edit_revision"] = (
                    st.session_state.get("ai_edit_revision", 0) + 1
                )
        except RateLimitedError as exc:
            st.warning(
                f"AI request limit reached. Try again in {int(exc.retry_after_seconds) + 1} seconds. Filters remain available."
            )
        except (OpenAIError, ValueError):
            st.error("The question could not be interpreted. Try a simpler search or use Filters.")
    if "ai_filters" not in st.session_state:
        return
    f = AISearchFilters(**st.session_state["ai_filters"])
    revision = st.session_state.get("ai_edit_revision", 0)
    with st.form(f"ai_review_{revision}"):
        st.subheader("Review & edit filters")
        name = st.text_input("Name", value=f.swimmer_name or "")
        a, b, c = st.columns(3)
        groups = ["All groups", *opts["groups"]]
        group = a.selectbox(
            "Group", groups, index=groups.index(f.group_label) if f.group_label in groups else 0
        )
        distance = b.number_input(
            "Distance (0 means any)", min_value=0, max_value=25000, value=f.distance or 0, step=25
        )
        strokes = [
            "All strokes",
            "Freestyle",
            "Backstroke",
            "Breaststroke",
            "Butterfly",
            "Individual Medley",
        ]
        stroke = c.selectbox("Stroke", strokes, index=strokes.index(f.stroke) if f.stroke else 0)
        a, b, c = st.columns(3)
        courses = ["All courses", "SCY", "SCM", "LCM"]
        course = a.selectbox(
            "Pool course", courses, index=courses.index(f.course) if f.course else 0
        )
        start = b.text_input("From date (YYYY-MM-DD)", value=f.date_from or "")
        end = c.text_input("To date (YYYY-MM-DD)", value=f.date_to or "")
        sort = st.selectbox(
            "Order", ["name", "fastest"], index=["name", "fastest"].index(f.sort_order)
        )
        limit = st.number_input("Maximum results", min_value=1, max_value=500, value=f.max_results)
        run = st.form_submit_button("Search with these filters", type="primary")
    if run:
        try:
            validated = AISearchFilters(
                swimmer_name=name or None,
                group_label=None if group == "All groups" else group,
                distance=distance or None,
                stroke=None if stroke == "All strokes" else stroke,
                course=None if course == "All courses" else course,
                date_from=start or None,
                date_to=end or None,
                sort_order=sort,
                max_results=limit,
            )
            st.session_state["ai_filters"] = validated.model_dump()
            st.session_state["ai_results_filters"] = dict(
                name=validated.swimmer_name,
                group_label=validated.group_label,
                distance_yards=validated.distance,
                stroke=validated.stroke,
                course=validated.course,
                date_from=validated.date_from,
                date_to=validated.date_to,
                sort_order=validated.sort_order,
                limit=validated.max_results,
            )
        except ValueError:
            st.error("Check the dates and filter values before searching.")
    if "ai_results_filters" in st.session_state:
        show_results(
            db.search_results(
                ctx.target(), **st.session_state["ai_results_filters"], team_ids=list(scope)
            ),
            key="ai",
        )
