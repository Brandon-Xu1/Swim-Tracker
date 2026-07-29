"""Parser for fixed-width Hy-Tek/Team Manager CL2 result files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re


STROKES = {
    "1": ("Free", "Freestyle"),
    "2": ("Back", "Backstroke"),
    "3": ("Breast", "Breaststroke"),
    "4": ("Fly", "Butterfly"),
    "5": ("IM", "Individual Medley"),
}


@dataclass(frozen=True, slots=True)
class SwimResult:
    source_file: str
    source_row: int
    athlete_id: str
    name: str
    age: int
    gender: str
    group_label: str
    event_id: str
    event: str
    distance_yards: int
    stroke: str
    time: str
    time_seconds: float
    course: str
    meet_date: str


def parse_time_to_seconds(value: str) -> float:
    """Convert a Team Manager time such as ``59.89`` or ``19:06.32``."""
    value = value.strip()
    if not value:
        raise ValueError("A swim time is required.")

    parts = value.split(":")
    if len(parts) == 1:
        seconds = float(parts[0])
    elif len(parts) == 2:
        seconds = int(parts[0]) * 60 + float(parts[1])
    else:
        raise ValueError(f"Unsupported swim time: {value!r}")

    if seconds < 0:
        raise ValueError("A swim time cannot be negative.")
    return seconds


def _parse_name(raw_name: str) -> str:
    """Convert ``Last, First Middle`` to the original app's ``First Last``."""
    parts = [part.strip() for part in raw_name.split(",", maxsplit=1)]
    if len(parts) != 2 or not parts[1]:
        return raw_name.strip()
    first_name = parts[1].split()[0]
    return f"{first_name} {parts[0]}".strip()


def _parse_age_gender(code: str) -> tuple[int, str]:
    match = re.fullmatch(r"(\d{1,2})(FF|MM)", code.strip())
    if not match:
        raise ValueError(f"Unsupported age/gender code: {code!r}")
    return int(match.group(1)), "F" if match.group(2) == "FF" else "M"


def _group_label(age: int, gender: str) -> str:
    if age <= 10:
        bracket = "10 and under"
    elif age <= 12:
        bracket = "11-12"
    elif age <= 14:
        bracket = "13-14"
    elif age <= 18:
        bracket = "15-18"
    else:
        bracket = str(age)
    return f"{'Girls' if gender == 'F' else 'Boys'} {bracket}"


def _parse_event(event_id: str) -> tuple[str, int, str]:
    if len(event_id) < 2 or not event_id.isdigit():
        raise ValueError(f"Unsupported event ID: {event_id!r}")

    short_stroke, full_stroke = STROKES.get(
        event_id[-1], ("Unknown", "Unknown")
    )
    distance = int(event_id[:-1])
    return f"{distance}-yard {short_stroke}", distance, full_stroke


def _parse_date(raw_date: str) -> str:
    return datetime.strptime(raw_date.strip(), "%m%d%Y").date().isoformat()


def parse_cl2_text(text: str, source_file: str = "uploaded.cl2") -> list[SwimResult]:
    """Parse completed individual results from CL2 text.

    D01 rows without a final time are scratches, disqualifications, or records
    without a completed result and are intentionally omitted.
    """
    results: list[SwimResult] = []

    for row_number, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("D01"):
            continue
        if len(line) < 97:
            continue

        raw_time = line[88:96].strip()
        if not raw_time:
            continue

        try:
            age, gender = _parse_age_gender(line[63:67])
            event_id = line[67:72].strip()
            event, distance, stroke = _parse_event(event_id)
            result = SwimResult(
                source_file=source_file,
                source_row=row_number,
                athlete_id=line[31:45].strip(),
                name=_parse_name(line[7:31]),
                age=age,
                gender=gender,
                group_label=_group_label(age, gender),
                event_id=event_id,
                event=event,
                distance_yards=distance,
                stroke=stroke,
                time=raw_time,
                time_seconds=parse_time_to_seconds(raw_time),
                course=line[96:97].strip() or "Unknown",
                meet_date=_parse_date(line[80:88]),
            )
        except (TypeError, ValueError):
            continue

        results.append(result)

    return results


def parse_cl2_file(path: str | Path) -> list[SwimResult]:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_cl2_text(text, source_file=path.name)
