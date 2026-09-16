import altair as alt
import pandas as pd
import streamlit as st

from .. import profiles
from ..parser import parse_time_to_seconds
from . import context as ctx


def page():
    st.title("Swimmer profiles")
    st.write("Follow progress across the meets in your workspace.")
    selected_team = ctx.team()
    if selected_team:
        dataset = st.radio("Results to explore", ["My team", "Public sample"], horizontal=True)
        scope = [selected_team["id"]] if dataset == "My team" else [0]
    else:
        scope = [0]
    st.caption(
        "Profiles and best times reflect imported meets only. Matching uses source identifiers and names; coaches can correct matches below."
    )
    name = st.text_input("Find a swimmer", placeholder="Search by name", max_chars=100)
    people = profiles.directory(ctx.target(), scope, name)
    if people.empty:
        st.info("No swimmers found. Import a meet or try another name.")
        return
    if len(people) > 500:
        st.caption("Showing the first 500 swimmers. Enter a name to narrow the list.")
        people = people.head(500)
    labels = {}
    name_counts = people["Swimmer"].value_counts()
    occurrences = {}
    for person in people.to_dict("records"):
        display = person["Swimmer"]
        occurrences[display] = occurrences.get(display, 0) + 1
        suffix = f" · profile {occurrences[display]}" if name_counts[display] > 1 else ""
        labels[person["id"]] = (
            f"{display}{suffix} · {person['Meets']} meets · {person['Results']} swims"
        )
    profile_id = st.selectbox("Swimmer", list(labels), format_func=labels.get)
    row = people[people.id == profile_id].iloc[0]
    frame = profiles.history(ctx.target(), profile_id, scope)
    if frame.empty:
        st.info("This profile has no remaining results.")
        return
    st.subheader(row["Swimmer"])
    a, b, c = st.columns(3)
    a.metric("Recorded swims", f"{len(frame):,}")
    b.metric("Imported meets", frame["Meet"].nunique())
    c.metric("Events & courses", len(frame[["Event", "Course"]].drop_duplicates()))
    st.caption(f"{frame['Date'].min()} — {frame['Date'].max()}")
    st.subheader("Best times in uploaded meets")
    st.dataframe(profiles.best_times(frame), hide_index=True, width="stretch")
    st.subheader("Progress")
    events = sorted(set(zip(frame["Event"], frame["Course"])))
    event, course = st.selectbox(
        "Event and course", events, format_func=lambda item: " · ".join(item)
    )
    subset = frame[(frame.Event == event) & (frame.Course == course)]
    progress = profiles.progression(frame, event, course)
    best = float(subset.Seconds.min())
    first = float(progress.Seconds.iloc[0])
    latest = float(progress.Seconds.iloc[-1])
    a, b, c = st.columns(3)
    a.metric("Best recorded", profiles.format_time(best))
    b.metric("Latest day’s best", profiles.format_time(latest))
    if len(progress) > 1:
        improvement = (first - latest) / first * 100
        c.metric(
            "Change from first day",
            f"{abs(improvement):.1f}% " + ("faster" if improvement >= 0 else "slower"),
        )
        chart = progress.copy()
        chart["Date"] = pd.to_datetime(chart.Date)
        chart = chart.rename(columns={"Seconds": "Daily best"}).melt(
            id_vars="Date", var_name="Series", value_name="Seconds"
        )
        trend = (
            alt.Chart(chart)
            .mark_line(point=True)
            .encode(
                x=alt.X("Date:T", title="Meet date", axis=alt.Axis(format="%b %d", tickCount=5)),
                y=alt.Y("Seconds:Q", title="Time (seconds)", scale=alt.Scale(zero=False)),
                color=alt.Color(
                    "Series:N",
                    scale=alt.Scale(
                        domain=["Daily best", "Best so far"], range=["#17a398", "#1f3b73"]
                    ),
                    legend=alt.Legend(title=None),
                ),
                strokeDash=alt.StrokeDash("Series:N", legend=None),
                tooltip=[
                    alt.Tooltip("Date:T", format="%Y-%m-%d"),
                    "Series:N",
                    alt.Tooltip("Seconds:Q", format=".2f"),
                ],
            )
            .properties(height=280)
        )
        st.altair_chart(trend, width="stretch")
        st.caption(
            "Each point is the fastest swim on that date. Lower is faster; the vertical scale follows recorded times. Different courses are never combined."
        )
    else:
        c.metric("Days recorded", "1")
        st.info("Import results from another date to see a progress trend.")
    goal = profiles.get_goal(ctx.target(), profile_id, event, course, scope)
    if goal:
        if best <= goal:
            st.success(
                f"Goal achieved: {profiles.format_time(goal)}. Best recorded: {profiles.format_time(best)}."
            )
        else:
            st.info(f"Goal: {profiles.format_time(goal)} · {best - goal:.2f} seconds to go.")
    editable = bool(selected_team and selected_team["id"] in scope and ctx.can_edit())
    if editable:
        with st.expander("Set a goal time"):
            with st.form(f"goal_{profile_id}_{event}_{course}"):
                value = st.text_input(
                    "Goal time (seconds or m:ss.hh)",
                    value=profiles.format_time(goal) if goal else "",
                )
                submitted = st.form_submit_button("Save goal")
            if submitted:
                try:
                    profiles.set_goal(
                        ctx.target(),
                        ctx.account(),
                        profile_id,
                        event,
                        course,
                        parse_time_to_seconds(value),
                    )
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
    st.subheader("Race history")
    st.dataframe(subset.drop(columns=["id", "Seconds"]), hide_index=True, width="stretch")
    st.download_button(
        "Download swimmer history",
        ctx.csv_bytes(frame.drop(columns=["id", "Seconds"])),
        "swimmer-history.csv",
        "text/csv",
    )
    if editable:
        with st.expander("Correct profile matching"):
            st.caption(
                "Confirm that both profiles represent the same swimmer before merging. Use Separate a meet to undo a mistaken match. Source identifiers may change between exports."
            )
            all_people = profiles.directory(ctx.target(), scope)
            candidates = {
                item["id"]: f"{item['Swimmer']} · {item['id'][:6]}"
                for item in all_people.to_dict("records")
                if item["id"] != profile_id
            }
            if candidates:
                with st.form(f"merge_{profile_id}"):
                    other = st.selectbox(
                        "Merge another profile into this swimmer",
                        list(candidates),
                        format_func=candidates.get,
                    )
                    confirm = st.checkbox("I verified these are the same swimmer")
                    submitted = st.form_submit_button("Merge profiles", disabled=not candidates)
                if submitted:
                    if not confirm:
                        st.warning("Confirm the identity match first.")
                    else:
                        try:
                            profiles.merge(ctx.target(), ctx.account(), other, profile_id)
                            st.rerun()
                        except (PermissionError, ValueError) as exc:
                            st.error(str(exc))
            if frame.Meet.nunique() > 1:
                with st.form(f"separate_{profile_id}"):
                    source = st.selectbox(
                        "Separate a meet into a new profile", sorted(frame.Meet.unique())
                    )
                    confirm = st.checkbox("I verified this meet belongs to a different swimmer")
                    submitted = st.form_submit_button("Separate meet")
                if submitted and confirm:
                    try:
                        profiles.separate_meet(ctx.target(), ctx.account(), profile_id, source)
                        st.rerun()
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))
