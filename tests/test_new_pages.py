import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import streamlit_app
from scripts.evaluate_ai import load_cases, score
from swim_tracker import accounts
from swim_tracker import database as db
from swim_tracker.ai_search import AISearchFilters
from swim_tracker.imports import import_meet
from swim_tracker.ui import context as ctx
from tests.test_workflows import payload


def profile_page():
    from swim_tracker.ui.profile_page import page

    page()


def account_page():
    from swim_tracker.ui.account_page import page

    page()


def search_page():
    from swim_tracker.ui.search_page import page

    page()


def import_page():
    from types import SimpleNamespace
    from unittest.mock import patch

    import streamlit_app
    from tests.test_workflows import payload

    upload = SimpleNamespace(name="preview.cl2", getvalue=lambda: payload() + b"\nD01broken")
    with patch("streamlit_app.st.file_uploader", return_value=[upload]):
        streamlit_app.data_page()


class PageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = str(Path(self.temp.name) / "ui.db")
        self.env = patch.dict(
            os.environ, {"SWIMTRACKER_DB_PATH": self.target, "OPENAI_API_KEY": ""}
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.owner, _ = accounts.register(self.target, "coach", "long-password")
        self.team = accounts.create_team(self.target, self.owner, "Page Team")
        import_meet(self.target, "first.cl2", payload(), team_id=self.team, account=self.owner)
        import_meet(
            self.target,
            "next.cl2",
            payload(meet_date="07152025", time="60.00"),
            team_id=self.team,
            account=self.owner,
        )

    def signed_in(self, fn):
        app = AppTest.from_function(fn, default_timeout=30)
        app.session_state["account"] = {"id": self.owner.id, "version": self.owner.version}
        app.session_state["active_team"] = self.team
        return app.run()

    def test_import_preview_and_partial_acknowledgment(self):
        app = self.signed_in(import_page)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("could not be parsed" in item.value for item in app.warning))
        next(button for button in app.button if button.label == "Import this meet").click()
        app.run()
        self.assertTrue(any("partial import" in item.value for item in app.error))
        next(box for box in app.checkbox if box.label.startswith("Import valid records")).check()
        next(button for button in app.button if button.label == "Import this meet").click()
        app.run()
        self.assertEqual(len(app.exception), 0)
        # The accepted row matches first.cl2: the preview path must deduplicate it.
        self.assertEqual(db.result_count(self.target, [self.team]), 2)
        self.assertIn("Already imported", app.session_state["flash"])

    def test_profile_progress_and_goal_flow(self):
        app = self.signed_in(profile_page)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("Jane Doe" in h.value for h in app.subheader))
        next(field for field in app.text_input if field.label.startswith("Goal time")).set_value(
            "59.00"
        )
        next(button for button in app.button if button.label == "Save goal").click()
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("Goal: 59.00" in item.value for item in app.info))

    def test_search_results_persist_on_another_rerun(self):
        app = self.signed_in(search_page)
        next(field for field in app.text_input if field.label == "Swimmer name").set_value(
            "Jane Doe"
        )
        next(button for button in app.button if button.label == "Search results").click()
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe), 1)
        app.run()
        self.assertEqual(len(app.dataframe), 1)
        reopened = AppTest.from_function(search_page, default_timeout=30)
        reopened.session_state["account"] = {"id": self.owner.id, "version": self.owner.version}
        reopened.session_state["active_team"] = self.team
        reopened.session_state["manual_filters"] = app.session_state["manual_filters"]
        reopened.run()
        self.assertEqual(
            next(field for field in reopened.text_input if field.label == "Swimmer name").value,
            "Jane Doe",
        )
        self.assertEqual(len(reopened.dataframe), 1)
        # Revoking membership removes team data on the next rerun.
        with db._engine(self.target).begin() as connection:
            connection.execute(db.metadata.tables["memberships"].delete())
        app.run()
        self.assertEqual(len(app.dataframe), 0)

    def test_ai_requires_review_and_retains_user_corrections(self):
        filters = AISearchFilters(
            intent="search",
            swimmer_name="Wrong Name",
            group_label=None,
            distance=100,
            stroke="Backstroke",
            course="LCM",
            date_from=None,
            date_to=None,
            sort_order="fastest",
            max_results=100,
        )
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch("swim_tracker.ui.search_page.interpret_cached", return_value=filters),
        ):
            app = self.signed_in(search_page)
            next(
                field for field in app.text_input if field.label == "Describe your search"
            ).set_value("Jane Doe 100 back")
            next(button for button in app.button if button.label == "Interpret question").click()
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.dataframe), 0)
            next(field for field in app.text_input if field.label == "Name").set_value("Jane Doe")
            next(
                button for button in app.button if button.label == "Search with these filters"
            ).click()
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.dataframe[0].value), 2)
            app.run()
            self.assertEqual(len(app.dataframe[0].value), 2)

    def test_new_team_created_from_account_page(self):
        app = self.signed_in(account_page)
        next(field for field in app.text_input if field.label == "Team name").set_value(
            "Second Team"
        )
        next(button for button in app.button if button.label == "Create team").click()
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(accounts.teams_for(self.target, self.owner)), 2)


class ReliabilityTests(unittest.TestCase):
    def test_unknown_legacy_schema_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "legacy.db"
            import sqlite3

            with sqlite3.connect(target) as c:
                c.execute("CREATE TABLE results (name TEXT)")
                c.execute("INSERT INTO results VALUES ('Preserve me')")
            with self.assertRaises(ValueError):
                db.initialize_database(target)
            with sqlite3.connect(target) as c:
                self.assertEqual(c.execute("SELECT name FROM results").fetchone()[0], "Preserve me")

    def test_existing_accounts_and_meets_survive_preparation(self):
        with tempfile.TemporaryDirectory() as folder:
            target = str(Path(folder) / "current.db")
            owner, _ = accounts.register(target, "existing", "long-password")
            team = accounts.create_team(target, owner, "Existing Team")
            import_meet(target, "existing.cl2", payload(), team_id=team, account=owner)
            with patch.dict(os.environ, {"SWIMTRACKER_DB_PATH": target}):
                streamlit_app.prepare_database()
                db._forget_engine(target)
                streamlit_app.prepare_database()
            self.assertIsNotNone(accounts.login(target, "existing", "long-password"))
            self.assertEqual(db.result_count(target, [team]), 1)
            self.assertEqual(db.get_meta(target, "schema_revision"), "2")

    def test_export_neutralizes_spreadsheet_formulas(self):
        import pandas as pd

        self.assertIn("'=1+1", ctx.csv_bytes(pd.DataFrame({"Name": ["=1+1"]})).decode())

    def test_eval_labels_and_scoring(self):
        data = load_cases(Path("evals/search_cases.json"))
        self.assertGreaterEqual(len(data["cases"]), 30)
        expected = data["cases"][0]["expected"]
        self.assertTrue(score(expected, expected)[0])
        self.assertFalse(score(expected, expected | {"swimmer_name": "Wrong Person"})[0])
