"""Regression tests for data integrity, authorization and profile calculations."""

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event
from unittest.mock import patch

from swim_tracker import accounts, profiles
from swim_tracker import database as db
from swim_tracker.imports import import_meet, inspect_upload, read_upload, remove_meet
from swim_tracker.parser import inspect_cl2_text, parse_time_to_seconds
from swim_tracker.rate_limit import RateLimitedError, SlidingWindowLimit
from swim_tracker.storage import acquire_quotas, transaction
from tests.test_parser import make_d01_line


def payload(name="Doe, Jane", time="1:02.33", course="L", meet_date="06152025", athlete="SAMPLEID"):
    line = make_d01_line(name=name, time=time, course=course, meet_date=meet_date)
    return (line[:31] + athlete.ljust(14)[:14] + line[45:]).encode()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "test.db"
        self.owner, self.recovery = accounts.register(self.target, "owner", "long-password")
        self.team = accounts.create_team(self.target, self.owner, "Test Team")

    def upload(self, filename="meet.cl2", content=None, **kwargs):
        return import_meet(
            self.target,
            filename,
            content or payload(),
            team_id=self.team,
            account=self.owner,
            **kwargs,
        )

    def test_renamed_duplicate_and_reimport_preserve_profile(self):
        self.upload()
        profile = profiles.directory(self.target, [self.team]).iloc[0].id
        duplicate = self.upload("renamed.cl2")
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.filename, "meet.cl2")
        self.upload()
        self.assertEqual(db.result_count(self.target, [self.team]), 1)
        self.assertEqual(profiles.directory(self.target, [self.team]).iloc[0].id, profile)

    def test_changed_filename_requires_explicit_replacement(self):
        self.upload()
        with self.assertRaises(ValueError):
            self.upload(content=payload(time="1:01.00"))
        self.assertEqual(
            db.search_results(self.target, team_ids=[self.team]).iloc[0]["Time"], "1:02.33"
        )
        self.upload(content=payload(time="1:01.00"), replace_existing=True)
        self.assertEqual(
            db.search_results(self.target, team_ids=[self.team]).iloc[0]["Time"], "1:01.00"
        )

    def test_legacy_duplicates_still_require_replacement_confirmation(self):
        for filename in ("first.cl2", "second.cl2"):
            db.replace_source_results(
                self.target,
                inspect_upload(filename, payload()).results,
                team_id=self.team,
            )
            db.save_raw_file(self.target, self.team, filename, payload())
        # Trigger metadata backfill: only one identical legacy file can own the fingerprint.
        self.upload("third.cl2", payload(time="60.00"))
        with db._engine(self.target).connect() as connection:
            sources = {
                row.filename for row in connection.execute(db.metadata.tables["imports"].select())
            }
        filename = ({"first.cl2", "second.cl2"} - sources).pop()
        with self.assertRaisesRegex(ValueError, "Confirm replacement"):
            self.upload(filename, payload(time="59.00"))
        self.assertEqual(db.get_raw_file(self.target, self.team, filename), payload())
        self.assertEqual(db.result_count(self.target, [self.team]), 3)
        self.upload(filename, payload(time="59.00"), replace_existing=True)
        self.assertEqual(db.get_raw_file(self.target, self.team, filename), payload(time="59.00"))
        self.assertEqual(db.result_count(self.target, [self.team]), 3)

    def test_profile_and_original_rollback_on_failure(self):
        self.upload()
        before = db.get_raw_file(self.target, self.team, "meet.cl2")
        with patch("swim_tracker.imports.link_rows", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                self.upload(content=payload(time="1:01.00"), replace_existing=True)
        self.assertEqual(db.get_raw_file(self.target, self.team, "meet.cl2"), before)
        self.assertEqual(
            db.search_results(self.target, team_ids=[self.team]).iloc[0]["Time"], "1:02.33"
        )
        self.assertEqual(len(profiles.directory(self.target, [self.team])), 1)

    def test_partial_import_requires_acknowledgment(self):
        raw = payload() + b"\nD01broken"
        with self.assertRaises(ValueError):
            self.upload(content=raw)
        report = inspect_upload("meet.cl2", raw)
        self.assertEqual(report.issues[0].line, 2)
        self.upload(content=raw, allow_partial=True)
        self.assertEqual(db.result_count(self.target, [self.team]), 1)

    def test_viewer_cannot_write_or_escalate_and_revocation_is_immediate(self):
        viewer, _ = accounts.register(self.target, "viewer", "long-password")
        invitation = accounts.invite(self.target, self.owner, self.team, "viewer")
        accounts.join(self.target, viewer, invitation)
        self.upload()
        profile = profiles.directory(self.target, [self.team]).iloc[0].id
        with self.assertRaises(PermissionError):
            import_meet(self.target, "other.cl2", payload(), team_id=self.team, account=viewer)
        with self.assertRaises(PermissionError):
            profiles.set_goal(self.target, viewer, profile, "100-meter Back", "LCM", 60)
        with self.assertRaises(PermissionError):
            accounts.invite(self.target, viewer, self.team, "coach")
        accounts.set_role(self.target, self.owner, self.team, viewer.id, "coach")
        profiles.set_goal(self.target, viewer, profile, "100-meter Back", "LCM", 60)
        accounts.set_role(self.target, self.owner, self.team, viewer.id, "remove")
        with self.assertRaises(PermissionError):
            remove_meet(self.target, "meet.cl2", team_id=self.team, account=viewer)
        self.assertEqual(accounts.teams_for(self.target, viewer), [])

    def test_recovery_is_one_time_and_revokes_existing_sessions(self):
        replacement = accounts.recover(self.target, "owner", self.recovery, "replacement-password")
        self.assertIsNone(accounts.current(self.target, self.owner.id, self.owner.version))
        with self.assertRaises(PermissionError):
            accounts.create_team(self.target, self.owner, "Another Team")
        with self.assertRaises(ValueError):
            accounts.recover(self.target, "owner", self.recovery, "another-password")
        self.assertIsNotNone(accounts.login(self.target, "owner", "replacement-password"))
        self.assertNotEqual(replacement, self.recovery)

    def test_invitation_one_use_and_expiry(self):
        viewer, _ = accounts.register(self.target, "viewer", "long-password")
        invitation = accounts.invite(self.target, self.owner, self.team, "viewer")
        with patch("swim_tracker.accounts.time.time", return_value=9999999999):
            with self.assertRaises(ValueError):
                accounts.join(self.target, viewer, invitation)
        accounts.join(self.target, viewer, invitation)
        another, _ = accounts.register(self.target, "another", "long-password")
        with self.assertRaises(ValueError):
            accounts.join(self.target, another, invitation)

    def test_legacy_team_claim_preserves_results(self):
        from swim_tracker.auth import register_team

        legacy = register_team(self.target, "Legacy Team", "legacy-password")
        from swim_tracker.parser import parse_cl2_text

        db.replace_source_results(self.target, parse_cl2_text(payload().decode()), team_id=legacy)
        self.assertEqual(
            accounts.claim_legacy_team(self.target, self.owner, "Legacy Team", "legacy-password"),
            legacy,
        )
        self.assertEqual(db.result_count(self.target, [legacy]), 1)
        with self.assertRaises(ValueError):
            accounts.claim_legacy_team(self.target, self.owner, "Legacy Team", "legacy-password")

    def test_matching_separates_namesakes_and_teams_and_allows_reviewed_merge(self):
        self.upload()
        self.upload("later.cl2", payload(time="1:00.00", meet_date="07152025"))
        directory = profiles.directory(self.target, [self.team])
        self.assertEqual(len(directory), 1)
        self.assertEqual(directory.iloc[0]["Meets"], 2)
        self.upload("namesake.cl2", payload(athlete="DIFFERENTID"))
        directory = profiles.directory(self.target, [self.team])
        self.assertEqual(len(directory), 2)
        first, second = directory.id.tolist()
        profiles.merge(self.target, self.owner, first, second)
        self.assertEqual(len(profiles.directory(self.target, [self.team])), 1)
        separated = profiles.separate_meet(self.target, self.owner, second, "namesake.cl2")
        self.upload("namesake.cl2", payload(athlete="DIFFERENTID"))
        self.assertEqual(len(profiles.history(self.target, separated, [self.team])), 1)
        # The original source-identity key remains mapped to the separated profile.
        self.assertNotEqual(separated, second)
        self.assertTrue(profiles.history(self.target, second, [0]).empty)
        other_team = accounts.create_team(self.target, self.owner, "Other Team")
        import_meet(self.target, "meet.cl2", payload(), team_id=other_team, account=self.owner)
        other_id = profiles.directory(self.target, [other_team]).iloc[0].id
        with self.assertRaises(ValueError):
            profiles.merge(self.target, self.owner, second, other_id)

    def test_goal_and_progress_keep_courses_separate(self):
        self.upload()
        self.upload("later.cl2", payload(time="1:00.00", meet_date="07152025"))
        self.upload("yards.cl2", payload(time="55.00", course="Y"))
        pid = profiles.directory(self.target, [self.team]).iloc[0].id
        frame = profiles.history(self.target, pid, [self.team])
        bests = profiles.best_times(frame)
        self.assertEqual(len(bests), 2)
        progress = profiles.progression(frame, "100-meter Back", "LCM")
        self.assertEqual(progress["Best so far"].tolist(), [62.33, 60.0])
        profiles.set_goal(self.target, self.owner, pid, "100-meter Back", "LCM", 59.0)
        self.assertEqual(
            profiles.get_goal(self.target, pid, "100-meter Back", "LCM", [self.team]), 59.0
        )
        self.assertIsNone(profiles.get_goal(self.target, pid, "100-meter Back", "LCM", [0]))

    def test_concurrent_same_upload_is_idempotent(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda _: self.upload(), range(2)))
        self.assertEqual(db.result_count(self.target, [self.team]), 1)

    def test_persistent_quota_and_atomic_parallel_reservations(self):
        limits = [("unit-test", [SlidingWindowLimit(2, 60)])]
        acquire_quotas(self.target, limits, now=100)
        db._forget_engine(self.target)
        acquire_quotas(self.target, limits, now=101)
        with self.assertRaises(RateLimitedError):
            acquire_quotas(self.target, limits, now=102)
        acquire_quotas(self.target, limits, now=161)

        def reserve(_):
            try:
                acquire_quotas(self.target, [("parallel", [SlidingWindowLimit(1, 60)])], now=200)
                return True
            except RateLimitedError:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sum(executor.map(reserve, range(2))), 1)

    def test_deletion_cleans_profile_links_and_originals(self):
        self.upload()
        remove_meet(self.target, "meet.cl2", team_id=self.team, account=self.owner)
        self.assertTrue(profiles.directory(self.target, [self.team]).empty)
        self.assertIsNone(db.get_raw_file(self.target, self.team, "meet.cl2"))

    def test_pagination_has_total_and_no_overlap(self):
        self.upload(content=b"\n".join(payload(time=f"{i}.00") for i in range(30, 40)))
        first = db.search_results(self.target, team_ids=[self.team], sort_order="fastest", limit=3)
        second = db.search_results(
            self.target, team_ids=[self.team], sort_order="fastest", limit=3, offset=3
        )
        self.assertEqual(first.attrs["total"], 10)
        self.assertFalse(set(first.Time) & set(second.Time))


class ImportFormatTests(unittest.TestCase):
    def test_report_distinguishes_excluded_from_invalid(self):
        report = inspect_cl2_text(make_d01_line(time="") + "\nD01broken\n" + payload().decode())
        self.assertEqual(report.excluded, 1)
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.individual_rows, 3)

    def test_nonfinite_and_invalid_clock_values_rejected(self):
        for value in ("nan", "inf", "-1", "0", "1:70", "-1:30"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_time_to_seconds(value)

    def test_zip_reads_cl2_without_extracting_paths(self):
        import io
        import zipfile

        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("../meet.cl2", payload())
            archive.writestr("ignore.txt", "anything")
        self.assertEqual(read_upload("files.zip", content.getvalue()), [("meet.cl2", payload())])
        with self.assertRaises(ValueError):
            read_upload("files.zip", b"not a zip")


@unittest.skipUnless(os.environ.get("SWIMTRACKER_TEST_DATABASE_URL"), "Postgres URL not configured")
class PostgresWorkflowTests(WorkflowTests):
    def test_goal_waits_for_concurrent_profile_edit(self):
        self.upload()
        profile = profiles.directory(self.target, [self.team]).iloc[0].id
        checked = Event()
        editable = profiles._editable

        def observed_editable(*args):
            team_id = editable(*args)
            checked.set()
            return team_id

        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction(self.target, f"team:{self.team}"):
                with patch("swim_tracker.profiles._editable", side_effect=observed_editable):
                    saved = executor.submit(
                        profiles.set_goal,
                        self.target,
                        self.owner,
                        profile,
                        "100-meter Back",
                        "LCM",
                        59.0,
                    )
                    self.assertTrue(checked.wait(timeout=5), "Goal save did not start")
                    with self.assertRaises(TimeoutError):
                        saved.result(timeout=0.25)
            saved.result(timeout=5)
        self.assertEqual(
            profiles.get_goal(self.target, profile, "100-meter Back", "LCM", [self.team]), 59.0
        )

    def setUp(self):
        self.target = os.environ["SWIMTRACKER_TEST_DATABASE_URL"]
        db._forget_engine(self.target)
        db.metadata.drop_all(db._engine(self.target))
        self.owner, self.recovery = accounts.register(self.target, "owner", "long-password")
        self.team = accounts.create_team(self.target, self.owner, "Test Team")

    def tearDown(self):
        db.metadata.drop_all(db._engine(self.target))
        db._forget_engine(self.target)
