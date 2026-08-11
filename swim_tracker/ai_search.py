"""OpenAI-backed natural-language interpretation for safe search filters."""

from __future__ import annotations

from datetime import date
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field, field_validator, model_validator


class AISearchFilters(BaseModel):
    swimmer_name: str | None
    group_label: str | None
    distance: int | None
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
    course: Literal["SCY", "SCM", "LCM"] | None
    date_from: str | None
    date_to: str | None
    sort_order: Literal["name", "fastest"]
    max_results: int = Field(ge=1, le=500)

    @field_validator("date_from", "date_to")
    @classmethod
    def _require_iso_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return date.fromisoformat(value).isoformat()

    @model_validator(mode="after")
    def _order_date_range(self) -> "AISearchFilters":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            self.date_from, self.date_to = self.date_to, self.date_from
        return self


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
                    "Individual Medley. Course means pool type: short course "
                    "yards is SCY, short course meters is SCM, long course "
                    "meters is LCM. Set distance to the event distance number "
                    "only, such as 100 for a 100 free. When the user names "
                    "dates or a date range, use ISO YYYY-MM-DD values in "
                    "date_from and date_to; use the same value for both when "
                    "one exact date is named, and null when no date is named. "
                    "Use fastest only when the user asks for fastest, best, "
                    "lowest, or quickest times. Set max_results to 100 unless "
                    "the user asks for another number. Available age/gender "
                    f"groups: {', '.join(available_groups)}."
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
