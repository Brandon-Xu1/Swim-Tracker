import unittest
from unittest.mock import Mock, patch

from swim_tracker.ai_search import AISearchFilters, interpret_search


def make_filters(**overrides) -> AISearchFilters:
    values = {
        "swimmer_name": "John Doe",
        "group_label": None,
        "distance": 100,
        "stroke": None,
        "course": None,
        "date_from": None,
        "date_to": None,
        "sort_order": "fastest",
        "max_results": 100,
    }
    values.update(overrides)
    return AISearchFilters(**values)


def client_returning(output_parsed) -> Mock:
    client = Mock()
    client.responses.parse.return_value = Mock(output_parsed=output_parsed)
    return client


class AISearchTests(unittest.TestCase):
    def test_interpretation_returns_validated_filters(self) -> None:
        expected = make_filters(
            course="SCY", date_from="2024-12-01", date_to="2024-12-31"
        )
        with patch(
            "swim_tracker.ai_search.OpenAI",
            return_value=client_returning(expected),
        ):
            actual = interpret_search(
                "John Doe's fastest 100 yard times in December 2024",
                api_key="test-key",
                model="test-model",
                available_groups=["Girls 11-12"],
            )

        self.assertEqual(actual, expected)

    def test_unparseable_output_raises_value_error(self) -> None:
        with patch(
            "swim_tracker.ai_search.OpenAI",
            return_value=client_returning(None),
        ):
            with self.assertRaises(ValueError):
                interpret_search(
                    "anything",
                    api_key="test-key",
                    model="test-model",
                    available_groups=["Girls 11-12"],
                )

    def test_hallucinated_group_is_rejected(self) -> None:
        hallucinated = make_filters(group_label="Girls 99-100")
        with patch(
            "swim_tracker.ai_search.OpenAI",
            return_value=client_returning(hallucinated),
        ):
            with self.assertRaises(ValueError):
                interpret_search(
                    "fastest girls 99-100 times",
                    api_key="test-key",
                    model="test-model",
                    available_groups=["Girls 11-12", "Boys 11-12"],
                )

    def test_invalid_date_is_rejected_as_value_error(self) -> None:
        # pydantic.ValidationError subclasses ValueError, so the app's
        # except (OpenAIError, ValueError) handler catches this too.
        with self.assertRaises(ValueError):
            make_filters(date_from="December 2024")

    def test_reversed_date_range_is_reordered(self) -> None:
        filters = make_filters(date_from="2024-12-31", date_to="2024-12-01")
        self.assertEqual(filters.date_from, "2024-12-01")
        self.assertEqual(filters.date_to, "2024-12-31")


if __name__ == "__main__":
    unittest.main()
