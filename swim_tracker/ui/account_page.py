import streamlit as st

from .. import accounts
from ..rate_limit import RateLimitedError
from . import context as ctx


def sidebar():
    user = ctx.account()
    if user:
        st.caption(f"Signed in as {user.username}")
        choices = accounts.teams_for(ctx.target(), user)
        ids = [None] + [row["id"] for row in choices]
        labels = {
            None: "Public sample",
            **{row["id"]: f"{row['name']} · {row['role']}" for row in choices},
        }
        if st.session_state.get("active_team") not in ids:
            st.session_state["active_team"] = None
        st.selectbox("Workspace", ids, format_func=labels.get, key="active_team")
        if st.button("Sign out", width="stretch"):
            ctx.reset_session()
            st.rerun()
    else:
        st.caption("Exploring the public sample")
        st.caption("Open Account to create a team or join one.")


def page():
    st.title("Account & team")
    if "recovery_code" in st.session_state:
        st.warning(
            "Save your recovery code somewhere private. It is shown only here and replaces email-based recovery."
        )
        st.code(st.session_state["recovery_code"])
        if st.button("I have saved my recovery code"):
            del st.session_state["recovery_code"]
            st.rerun()
    try:
        _render()
    except RateLimitedError as exc:
        st.warning(f"Too many attempts. Try again in {int(exc.retry_after_seconds) + 1} seconds.")
    except (ValueError, PermissionError) as exc:
        st.error(str(exc))


def _render():
    user = ctx.account()
    if not user:
        st.write("Use your own account. Team owners can invite coaches and viewers.")
        sign_in, register, recover = st.tabs(["Sign in", "Create account", "Recover account"])
        with sign_in:
            with st.form("sign_in"):
                username = st.text_input("Username", max_chars=40)
                password = st.text_input("Password", type="password", max_chars=256)
                submitted = st.form_submit_button("Sign in", type="primary")
            if submitted:
                user = accounts.login(ctx.target(), username, password)
                if user:
                    ctx.reset_session(user)
                    st.rerun()
                st.error("Username or password is incorrect.")
        with register:
            with st.form("register"):
                username = st.text_input("Choose a username", max_chars=40)
                password = st.text_input(
                    "Choose a password (10+ characters)", type="password", max_chars=256
                )
                confirm = st.text_input("Repeat password", type="password", max_chars=256)
                submitted = st.form_submit_button("Create account", type="primary")
            if submitted:
                if password != confirm:
                    raise ValueError("Passwords do not match.")
                user, recovery = accounts.register(ctx.target(), username, password)
                ctx.reset_session(user)
                st.session_state["recovery_code"] = recovery
                st.rerun()
        with recover:
            st.caption(
                "Recovery codes are single-use. Recovering your account signs out all existing sessions."
            )
            with st.form("recover"):
                username = st.text_input("Account username", max_chars=40)
                recovery = st.text_input("Recovery code", type="password", max_chars=100)
                password = st.text_input("New password", type="password", max_chars=256)
                confirm = st.text_input("Confirm new password", type="password", max_chars=256)
                submitted = st.form_submit_button("Reset password")
            if submitted:
                if password != confirm:
                    raise ValueError("Passwords do not match.")
                st.session_state["recovery_code"] = accounts.recover(
                    ctx.target(), username, recovery, password
                )
                st.success("Password reset. Save your replacement recovery code, then sign in.")
                st.rerun()
        return
    st.write(f"Your account: **{user.username}**")
    create, join = st.columns(2)
    with create:
        with st.form("create_team"):
            st.subheader("Create a team")
            name = st.text_input("Team name", max_chars=40)
            submitted = st.form_submit_button("Create team", type="primary")
        if submitted:
            team_id = accounts.create_team(ctx.target(), user, name)
            # Sidebar widget has already rendered; select the workspace on the next run.
            st.session_state["next_team"] = team_id
            st.rerun()
    with join:
        with st.form("join_team"):
            st.subheader("Join a team")
            token = st.text_input("Invitation code", type="password", max_chars=100)
            submitted = st.form_submit_button("Join team")
        if submitted:
            st.session_state["next_team"] = accounts.join(ctx.target(), user, token)
            st.rerun()
    with st.expander("Migrate an existing team account"):
        st.caption(
            "The first owner can claim an existing team using its old password. Imported data stays in place; future access uses individual accounts."
        )
        with st.form("claim_team"):
            name = st.text_input("Existing team name", max_chars=40)
            password = st.text_input("Legacy team password", type="password", max_chars=256)
            submitted = st.form_submit_button("Claim team")
        if submitted:
            st.session_state["next_team"] = accounts.claim_legacy_team(
                ctx.target(), user, name, password
            )
            st.rerun()
    selected = ctx.team()
    if selected and selected["role"] == "owner":
        st.divider()
        st.subheader(f"Manage {selected['name']}")
        st.caption(
            "Coaches can import meets and edit profiles. Viewers can search, view profiles, and download results."
        )
        with st.form("invite"):
            role = st.selectbox("Invitation role", ["viewer", "coach"])
            submitted = st.form_submit_button("Create one-time invitation")
        if submitted:
            token = accounts.invite(ctx.target(), user, selected["id"], role)
            st.code(token)
            st.caption("Share this code with one person. It expires in seven days.")
        people = accounts.members(ctx.target(), user, selected["id"])
        st.dataframe(
            [dict(row) for row in people],
            hide_index=True,
            column_order=["username", "role"],
            width="stretch",
        )
        editable = {row["id"]: row["username"] for row in people if row["role"] != "owner"}
        if editable:
            with st.form("member_role"):
                person = st.selectbox("Member", list(editable), format_func=editable.get)
                role = st.selectbox("New permission", ["viewer", "coach", "remove"])
                submitted = st.form_submit_button("Update member")
            if submitted:
                accounts.set_role(ctx.target(), user, selected["id"], person, role)
                st.rerun()
    with st.expander("Change password"):
        with st.form("change_password"):
            old = st.text_input("Current password", type="password", max_chars=256)
            new = st.text_input("Replacement password", type="password", max_chars=256)
            confirm = st.text_input("Repeat replacement password", type="password", max_chars=256)
            submitted = st.form_submit_button("Change password and sign out")
        if submitted:
            if new != confirm:
                raise ValueError("Passwords do not match.")
            accounts.change_password(ctx.target(), user, old, new)
            ctx.reset_session()
            st.rerun()
