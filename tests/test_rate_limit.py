import unittest
from unittest.mock import patch

import streamlit as st

import streamlit_app
from swim_tracker.ai_search import AISearchFilters
from swim_tracker.rate_limit import (
    RateLimitedError,
    SlidingWindowLimit,
    try_acquire,
)


class RateLimitTests(unittest.TestCase):
    def test_allows_calls_under_the_limit(self) -> None:
        limits = [SlidingWindowLimit(max_calls=3, window_seconds=60.0)]
        history: list[float] = []
        for second in range(3):
            self.assertEqual(try_acquire(limits, history, float(second)), 0.0)
        self.assertEqual(len(history), 3)

    def test_blocks_at_the_limit_with_correct_wait(self) -> None:
        limits = [SlidingWindowLimit(max_calls=2, window_seconds=60.0)]
        history: list[float] = []
        try_acquire(limits, history, 0.0)
        try_acquire(limits, history, 10.0)
        wait = try_acquire(limits, history, 30.0)
        # The oldest call at t=0 expires at t=60, so 30 seconds remain.
        self.assertAlmostEqual(wait, 30.0)
        self.assertEqual(len(history), 2)

    def test_expired_calls_free_the_window(self) -> None:
        limits = [SlidingWindowLimit(max_calls=1, window_seconds=60.0)]
        history: list[float] = []
        try_acquire(limits, history, 0.0)
        self.assertGreater(try_acquire(limits, history, 59.0), 0.0)
        self.assertEqual(try_acquire(limits, history, 61.0), 0.0)

    def test_longest_window_governs_when_short_window_allows(self) -> None:
        limits = [
            SlidingWindowLimit(max_calls=2, window_seconds=10.0),
            SlidingWindowLimit(max_calls=3, window_seconds=3600.0),
        ]
        history: list[float] = []
        for now in (0.0, 100.0, 200.0):
            self.assertEqual(try_acquire(limits, history, now), 0.0)
        wait = try_acquire(limits, history, 300.0)
        # Hourly cap of 3 is hit; the t=0 call frees a slot at t=3600.
        self.assertAlmostEqual(wait, 3300.0)


class CachedInterpretationTests(unittest.TestCase):
    """The cache and the rate limit combine so hits never spend quota."""

    def setUp(self) -> None:
        streamlit_app._interpret_cached.clear()
        st.session_state.pop(streamlit_app.AI_HISTORY_SESSION_KEY, None)
        self.calls: list[str] = []
        self.filters = AISearchFilters(
            swimmer_name=None,
            group_label=None,
            distance=100,
            stroke=None,
            course=None,
            date_from=None,
            date_to=None,
            sort_order="fastest",
            max_results=100,
        )

    def _interpret(self, query: str) -> AISearchFilters:
        return streamlit_app._interpret_cached(
            query, "test-model", ("Girls 11-12",), "test-key"
        )

    def _fake(self, query, *, api_key, model, available_groups):
        self.calls.append(query)
        return self.filters

    def test_identical_questions_hit_openai_once(self) -> None:
        with patch.object(
            streamlit_app, "interpret_search", side_effect=self._fake
        ):
            self._interpret("fastest 100 free")
            self._interpret("fastest 100 free")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            len(st.session_state[streamlit_app.AI_HISTORY_SESSION_KEY]), 1
        )

    def test_sixth_distinct_question_in_a_minute_is_limited(self) -> None:
        with patch.object(
            streamlit_app, "interpret_search", side_effect=self._fake
        ):
            for index in range(5):
                self._interpret(f"question {index}")
            with self.assertRaises(RateLimitedError):
                self._interpret("one too many")
        self.assertEqual(len(self.calls), 5)


if __name__ == "__main__":
    unittest.main()
