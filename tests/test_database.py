import os
from pathlib import Path
import tempfile
import unittest

from swim_tracker.database import (
    _engine,
    delete_source_results,
    metadata,
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
    """Runs against a temporary SQLite file, the default backend."""

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

    def test_filters_by_course(self) -> None:
        replace_source_results(self.database_path, self.results)
        scy_matches = search_results(
            self.database_path, course="SCY", limit=1000
        )
        lcm_matches = search_results(self.database_path, course="LCM")
        self.assertFalse(scy_matches.empty)
        self.assertTrue((scy_matches["Course"] == "SCY").all())
        self.assertTrue(lcm_matches.empty)
        with self.assertRaises(ValueError):
            search_results(self.database_path, course="short course")

    def test_filters_by_date_range(self) -> None:
        replace_source_results(self.database_path, self.results)
        all_dates = {result.meet_date for result in self.results}
        self.assertGreater(len(all_dates), 1)
        last_date = max(all_dates)

        matches = search_results(
            self.database_path,
            date_from=last_date,
            date_to=last_date,
            limit=1000,
        )
        self.assertFalse(matches.empty)
        self.assertTrue((matches["Date"] == last_date).all())

        none_matched = search_results(
            self.database_path, date_from="2030-01-01"
        )
        self.assertTrue(none_matched.empty)

    def test_delete_source_removes_only_that_source(self) -> None:
        replace_source_results(self.database_path, self.results)
        removed = delete_source_results(
            self.database_path, self.results[0].source_file
        )
        self.assertEqual(removed, 3999)
        self.assertEqual(result_count(self.database_path), 0)
        self.assertEqual(
            delete_source_results(self.database_path, "missing.cl2"), 0
        )

    def test_name_input_cannot_be_executed_as_sql(self) -> None:
        replace_source_results(self.database_path, self.results)
        matches = search_results(
            self.database_path,
            name="'; DROP TABLE results; --",
        )
        self.assertTrue(matches.empty)
        self.assertEqual(result_count(self.database_path), 3999)


@unittest.skipUnless(
    os.environ.get("SWIMTRACKER_TEST_DATABASE_URL"),
    "Set SWIMTRACKER_TEST_DATABASE_URL to a Postgres URL to run these",
)
class PostgresDatabaseTests(DatabaseTests):
    """Runs the identical suite against a real Postgres database."""

    def setUp(self) -> None:
        self.database_path = os.environ["SWIMTRACKER_TEST_DATABASE_URL"]
        self.results = parse_cl2_file(DATA_FILE)
        metadata.drop_all(_engine(self.database_path))

    def tearDown(self) -> None:
        metadata.drop_all(_engine(self.database_path))


if __name__ == "__main__":
    unittest.main()
