from pathlib import Path
import tempfile
import unittest

from swim_tracker.database import (
    replace_source_results,
    result_count,
    search_results,
)
from swim_tracker.parser import parse_cl2_file


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = (
    ROOT / "Meet Results-2024 TAC TITANS Jingle Bells Meet-20Dec2024-001.cl2"
)


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "test.db"
        self.results = parse_cl2_file(DATA_FILE)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_reimport_is_idempotent(self) -> None:
        replace_source_results(self.database_path, self.results)
        replace_source_results(self.database_path, self.results)
        self.assertEqual(result_count(self.database_path), 3999)

    def test_searches_by_name_and_event(self) -> None:
        replace_source_results(self.database_path, self.results)
        matches = search_results(
            self.database_path,
            name="Pierce Arora",
            distance_yards=100,
            stroke="Freestyle",
        )
        self.assertFalse(matches.empty)
        self.assertTrue((matches["Name"] == "Pierce Arora").all())
        self.assertTrue((matches["Event"] == "100-yard Free").all())

    def test_name_input_cannot_be_executed_as_sql(self) -> None:
        replace_source_results(self.database_path, self.results)
        matches = search_results(
            self.database_path,
            name="'; DROP TABLE results; --",
        )
        self.assertTrue(matches.empty)
        self.assertEqual(result_count(self.database_path), 3999)


if __name__ == "__main__":
    unittest.main()
