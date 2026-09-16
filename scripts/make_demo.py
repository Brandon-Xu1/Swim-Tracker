"""Generate a clearly fictional four-meet season for demos and usability testing."""

import argparse
from pathlib import Path

from swim_tracker.profiles import format_time


def generate(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for month in range(1, 5):
        lines = [
            "SYNTHETIC DEMO DATA — fictional swimmers and performances; not official meet results."
        ]
        for athlete, name in enumerate(["Sample, Morgan", "Example, Alex", "Demo, Jordan"]):
            for event_id, initial in [("1001", 65.0), ("2005", 155.0), ("1002", 76.0)]:
                seconds = initial + athlete * 3.2 - (month - 1) * (0.9 + athlete * 0.1)
                lines.append(
                    "D01"
                    + " " * 4
                    + name.ljust(24)
                    + f"DEMO{athlete:04d}".ljust(14)
                    + " " * 18
                    + "12FF"
                    + event_id.ljust(5)
                    + " " * 8
                    + f"{month:02d}152025"
                    + format_time(seconds).rjust(8)
                    + "Y"
                )
        (folder / f"SYNTHETIC-season-{month}.cl2").write_text("\n".join(lines) + "\n")
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/demo"))
    args = parser.parse_args()
    print(generate(args.output))
