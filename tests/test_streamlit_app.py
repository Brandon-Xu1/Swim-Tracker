import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from swim_tracker.database import delete_source_results


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE_NAME = "Meet Results-2024 TAC TITANS Jingle Bells Meet-20Dec2024-001.cl2"


class StreamlitAppTests(unittest.TestCase):
    def test_app_starts_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = str(Path(directory) / "app.db")
            environment = {
                "SWIMTRACKER_DB_PATH": database_path,
                "OPENAI_API_KEY": "",
            }
            with patch.dict(os.environ, environment, clear=False):
                app = AppTest.from_file(
                    str(ROOT / "streamlit_app.py"), default_timeout=30
                ).run()

                self.assertEqual(len(app.exception), 0)
                self.assertIn("Swim Tracker", [title.value for title in app.title])
                self.assertTrue(
                    any("3,999" in metric.value for metric in app.metric)
                )

                next(
                    field
                    for field in app.text_input
                    if field.label == "Swimmer name"
                ).set_value("Pierce Arora")
                next(
                    box for box in app.selectbox if box.label == "Event"
                ).select("100-yard Free")
                next(
                    button
                    for button in app.button
                    if button.label == "Search results"
                ).click()
                app.run()

                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.dataframe), 1)
                self.assertEqual(app.dataframe[0].value.iloc[0]["Time"], "52.08")

    def test_deleted_data_stays_deleted_across_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = str(Path(directory) / "app.db")
            environment = {
                "SWIMTRACKER_DB_PATH": database_path,
                "OPENAI_API_KEY": "",
            }
            with patch.dict(os.environ, environment, clear=False):
                first_run = AppTest.from_file(
                    str(ROOT / "streamlit_app.py"), default_timeout=30
                ).run()
                self.assertEqual(len(first_run.exception), 0)
                self.assertTrue(
                    any("3,999" in metric.value for metric in first_run.metric)
                )

                delete_source_results(database_path, DATA_FILE_NAME)

                second_run = AppTest.from_file(
                    str(ROOT / "streamlit_app.py"), default_timeout=30
                ).run()
                self.assertEqual(len(second_run.exception), 0)
                self.assertTrue(
                    any(metric.value == "0" for metric in second_run.metric)
                )


if __name__ == "__main__":
    unittest.main()
