import unittest
from unittest.mock import Mock, patch

from swim_tracker.ai_search import AISearchFilters, interpret_search


class AISearchTests(unittest.TestCase):
    def test_interpretation_returns_validated_filters(self) -> None:
        expected = AISearchFilters(
            swimmer_name="Natalie Xu",
            group_label=None,
            distance_yards=100,
            stroke=None,
            sort_order="fastest",
            max_results=100,
        )
        response = Mock(output_parsed=expected)
        client = Mock()
        client.responses.parse.return_value = response

        with patch("swim_tracker.ai_search.OpenAI", return_value=client):
            actual = interpret_search(
                "Natalie Xu's fastest 100 yard times",
                api_key="test-key",
                model="test-model",
                available_groups=["Girls 11-12"],
            )

        self.assertEqual(actual, expected)
        client.responses.parse.assert_called_once()


if __name__ == "__main__":
    unittest.main()
