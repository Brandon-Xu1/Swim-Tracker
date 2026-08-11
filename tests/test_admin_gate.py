import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import streamlit_app


def _run_data_page() -> None:
    import streamlit_app

    streamlit_app.data_page()


class AdminGateUnitTests(unittest.TestCase):
    def test_verify_admin_password(self) -> None:
        self.assertTrue(streamlit_app.verify_admin_password("secret", "secret"))
        self.assertFalse(streamlit_app.verify_admin_password("wrong", "secret"))
        self.assertFalse(streamlit_app.verify_admin_password("", "secret"))

    def test_unlocked_when_no_password_is_configured(self) -> None:
        with patch.dict(os.environ, {"ADMIN_PASSWORD": ""}, clear=False):
            self.assertTrue(streamlit_app.admin_unlocked(session={}))

    def test_locked_until_session_is_marked_unlocked(self) -> None:
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "hunter22"}, clear=False):
            self.assertFalse(streamlit_app.admin_unlocked(session={}))
            self.assertTrue(
                streamlit_app.admin_unlocked(
                    session={streamlit_app.ADMIN_SESSION_KEY: True}
                )
            )


class AdminGatePageTests(unittest.TestCase):
    def test_data_page_write_controls_require_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "SWIMTRACKER_DB_PATH": str(Path(directory) / "gate.db"),
                "OPENAI_API_KEY": "",
                "ADMIN_PASSWORD": "hunter22",
            }
            with patch.dict(os.environ, environment, clear=False):
                app = AppTest.from_function(_run_data_page)
                app.default_timeout = 30
                app.run()

                self.assertEqual(len(app.exception), 0)
                labels = [button.label for button in app.button]
                self.assertFalse(
                    any("Reload bundled" in label for label in labels)
                )

                app.text_input[0].set_value("wrong")
                next(
                    button
                    for button in app.button
                    if "Unlock" in button.label
                ).click()
                app.run()
                self.assertEqual(len(app.error), 1)

                app.text_input[0].set_value("hunter22")
                next(
                    button
                    for button in app.button
                    if "Unlock" in button.label
                ).click()
                app.run()
                labels = [button.label for button in app.button]
                self.assertTrue(
                    any("Reload bundled" in label for label in labels)
                )


if __name__ == "__main__":
    unittest.main()
