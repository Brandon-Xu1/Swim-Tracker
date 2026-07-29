from pathlib import Path
import unittest

from swim_tracker.parser import parse_cl2_file, parse_time_to_seconds


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

    def test_time_conversion(self) -> None:
        self.assertEqual(parse_time_to_seconds("59.89"), 59.89)
        self.assertEqual(parse_time_to_seconds("2:13.07"), 133.07)
        with self.assertRaises(ValueError):
            parse_time_to_seconds("")


if __name__ == "__main__":
    unittest.main()
