import os
from pathlib import Path
import tempfile
import unittest

from swim_tracker import database
from swim_tracker.auth import (
    authenticate_team,
    hash_password,
    register_team,
    verify_password,
)
from swim_tracker.database import (
    PUBLIC_TEAM_ID,
    delete_source_results,
    get_raw_file,
    replace_source_results,
    result_count,
    save_raw_file,
    search_results,
    source_summary,
)
from swim_tracker.parser import parse_cl2_text

from tests.test_parser import make_d01_line


def parsed_meet(source_file: str, names: list[str]) -> list:
    lines = "\n".join(make_d01_line(name=name) for name in names)
    return parse_cl2_text(lines, source_file=source_file)


class PasswordTests(unittest.TestCase):
    def test_hash_roundtrip(self) -> None:
        stored = hash_password("correct horse")
        self.assertTrue(verify_password("correct horse", stored))
        self.assertFalse(verify_password("wrong horse", stored))

    def test_hashes_are_salted(self) -> None:
        self.assertNotEqual(
            hash_password("same password"), hash_password("same password")
        )

    def test_malformed_stored_hash_never_verifies(self) -> None:
        self.assertFalse(verify_password("anything", "not-a-real-hash"))
        self.assertFalse(verify_password("anything", ""))


class MultiTenancyTests(unittest.TestCase):
    """Runs against a temporary SQLite file, the default backend."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.target = str(Path(self.temp_directory.name) / "tenancy.db")

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_register_and_authenticate(self) -> None:
        team_id = register_team(self.target, "  TAC   Titans ", "password123")
        account = authenticate_team(self.target, "tac titans", "password123")
        self.assertEqual(account, (team_id, "TAC Titans"))
        self.assertIsNone(
            authenticate_team(self.target, "TAC Titans", "wrong password")
        )
        self.assertIsNone(
            authenticate_team(self.target, "No Such Team", "password123")
        )

    def test_registration_validation(self) -> None:
        with self.assertRaises(ValueError):
            register_team(self.target, "ab", "password123")
        with self.assertRaises(ValueError):
            register_team(self.target, "Valid Name", "short")
        register_team(self.target, "Valid Name", "password123")
        with self.assertRaises(ValueError):
            register_team(self.target, "VALID  name", "password123")

    def test_results_are_isolated_per_team(self) -> None:
        team_a = register_team(self.target, "Team A", "password123")
        team_b = register_team(self.target, "Team B", "password123")
        replace_source_results(
            self.target,
            parsed_meet("meet.cl2", ["Aardvark, Alice", "Aardvark, Amy"]),
            team_id=team_a,
        )
        replace_source_results(
            self.target,
            parsed_meet("meet.cl2", ["Badger, Bob"]),
            team_id=team_b,
        )

        seen_by_a = search_results(
            self.target, team_ids=[PUBLIC_TEAM_ID, team_a]
        )
        self.assertEqual(len(seen_by_a), 2)
        self.assertTrue(seen_by_a["Name"].str.startswith("A").all())

        anonymous = search_results(self.target)
        self.assertTrue(anonymous.empty)

        self.assertEqual(result_count(self.target), 0)
        self.assertEqual(
            result_count(self.target, team_ids=[PUBLIC_TEAM_ID, team_b]), 1
        )
        self.assertEqual(
            list(source_summary(self.target, team_id=team_a)["Source file"]),
            ["meet.cl2"],
        )

        delete_source_results(self.target, "meet.cl2", team_id=team_a)
        self.assertEqual(
            result_count(self.target, team_ids=[team_a]), 0
        )
        self.assertEqual(
            result_count(self.target, team_ids=[team_b]), 1
        )

    def test_raw_files_roundtrip_and_follow_deletion(self) -> None:
        team_id = register_team(self.target, "Team C", "password123")
        save_raw_file(self.target, team_id, "meet.cl2", b"original bytes")
        self.assertEqual(
            get_raw_file(self.target, team_id, "meet.cl2"), b"original bytes"
        )
        self.assertIsNone(
            get_raw_file(self.target, PUBLIC_TEAM_ID, "meet.cl2")
        )

        save_raw_file(self.target, team_id, "meet.cl2", b"replaced bytes")
        self.assertEqual(
            get_raw_file(self.target, team_id, "meet.cl2"), b"replaced bytes"
        )

        delete_source_results(self.target, "meet.cl2", team_id=team_id)
        self.assertIsNone(get_raw_file(self.target, team_id, "meet.cl2"))


@unittest.skipUnless(
    os.environ.get("SWIMTRACKER_TEST_DATABASE_URL"),
    "Set SWIMTRACKER_TEST_DATABASE_URL to a Postgres URL to run these",
)
class PostgresMultiTenancyTests(MultiTenancyTests):
    """Runs the identical suite against a real Postgres database."""

    def setUp(self) -> None:
        self.target = os.environ["SWIMTRACKER_TEST_DATABASE_URL"]
        database.metadata.drop_all(database._engine(self.target))

    def tearDown(self) -> None:
        database.metadata.drop_all(database._engine(self.target))


if __name__ == "__main__":
    unittest.main()
