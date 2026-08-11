from pathlib import Path
import unittest

from swim_tracker.parser import (
    parse_cl2_file,
    parse_cl2_text,
    parse_time_to_seconds,
)


def make_d01_line(
    *,
    name: str = "Doe, Jane",
    age_gender: str = "12FF",
    event_id: str = "1002",
    meet_date: str = "06152025",
    time: str = "1:02.33",
    course: str = "L",
) -> str:
    return (
        "D01"
        + " " * 4
        + name.ljust(24)[:24]
        + "SAMPLEID".ljust(14)[:14]
        + " " * 18
        + age_gender.ljust(4)[:4]
        + event_id.ljust(5)[:5]
        + " " * 8
        + meet_date
        + time.rjust(8)[:8]
        + course
    )


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = (
    ROOT / "Meet Results-2024 TAC TITANS Jingle Bells Meet-20Dec2024-001.cl2"
)


class ParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.results = parse_cl2_file(DATA_FILE)

    def test_parses_all_completed_results(self) -> None:
        self.assertEqual(len(self.results), 3999)

    def test_parses_full_five_character_event_id(self) -> None:
        mile_results = [
            result for result in self.results if result.event_id == "16501"
        ]
        self.assertEqual(len(mile_results), 2)
        self.assertEqual(mile_results[0].event, "1650-yard Free")
        self.assertEqual(mile_results[0].distance_yards, 1650)

    def test_preserves_eight_character_time(self) -> None:
        long_result = next(
            result for result in self.results if result.time == "19:06.32"
        )
        self.assertAlmostEqual(long_result.time_seconds, 1146.32)

    def test_meter_courses_are_labeled_in_meters(self) -> None:
        long_course = parse_cl2_text(make_d01_line(course="L"))
        short_course_meters = parse_cl2_text(make_d01_line(course="S"))
        short_course_yards = parse_cl2_text(make_d01_line(course="Y"))

        self.assertEqual(long_course[0].event, "100-meter Back")
        self.assertEqual(long_course[0].course, "L")
        self.assertEqual(long_course[0].name, "Jane Doe")
        self.assertEqual(long_course[0].meet_date, "2025-06-15")
        self.assertEqual(short_course_meters[0].event, "100-meter Back")
        self.assertEqual(short_course_yards[0].event, "100-yard Back")

    def test_time_conversion(self) -> None:
        self.assertEqual(parse_time_to_seconds("59.89"), 59.89)
        self.assertEqual(parse_time_to_seconds("2:13.07"), 133.07)
        with self.assertRaises(ValueError):
            parse_time_to_seconds("")


if __name__ == "__main__":
    unittest.main()
