import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


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

                app.text_input[0].set_value("Pierce Arora")
                app.selectbox[1].select("100-yard Free")
                next(
                    button
                    for button in app.button
                    if button.label == "Search results"
                ).click()
                app.run()

                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.dataframe), 1)
                self.assertEqual(app.dataframe[0].value.iloc[0]["Time"], "52.08")


if __name__ == "__main__":
    unittest.main()
