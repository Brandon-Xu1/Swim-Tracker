"""OpenAI-backed natural-language interpretation for safe search filters."""

from __future__ import annotations

from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field


class AISearchFilters(BaseModel):
    swimmer_name: str | None
    group_label: str | None
    distance_yards: int | None
    stroke: (
        Literal[
            "Freestyle",
            "Backstroke",
            "Breaststroke",
            "Butterfly",
            "Individual Medley",
        ]
        | None
    )
    sort_order: Literal["name", "fastest"]
    max_results: int = Field(ge=1, le=500)


def interpret_search(
    query: str,
    *,
    api_key: str,
    model: str,
    available_groups: list[str],
) -> AISearchFilters:
    """Translate natural language to validated filters, never executable SQL."""
    client = OpenAI(api_key=api_key)
    response = client.responses.parse(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "Convert a swim-results question into search filters. "
                    "Use null for filters the user did not request. "
                    "Map free/free style to Freestyle, back to Backstroke, "
                    "breast to Breaststroke, fly to Butterfly, and IM to "
                    "Individual Medley. Use fastest only when the user asks for "
                    "fastest, best, lowest, or quickest times. Set max_results "
                    "to 100 unless the user asks for another number. Available age/"
                    f"gender groups: {', '.join(available_groups)}."
                ),
            },
            {"role": "user", "content": query},
        ],
        text_format=AISearchFilters,
    )
    if response.output_parsed is None:
        raise ValueError("The model did not return usable search filters.")
    filters = response.output_parsed
    if (
        filters.group_label is not None
        and filters.group_label not in available_groups
    ):
        raise ValueError("The model returned an age group that is not in the data.")
    return filters
