"""Writes tidewatch, a small and plausible Python project, into the given folder.

Five modules of tide, chart, buoy, alert and harbor helpers, about 45 KB each, so
reading a couple of them grows a session's context visibly. The output is the same
on every run.

Usage: python3 -I tidewatch.py <folder>
"""
import os
import random
import sys

MODULES = {
    "tides.py": ("TideStation", "tide height", "station", "m", "harmonic constants"),
    "charts.py": ("ChartTile", "depth sounding", "tile", "m", "survey grid"),
    "buoys.py": ("BuoyReading", "wave height", "buoy", "m", "spectral bands"),
    "alerts.py": ("SmallCraftAlert", "wind gust", "zone", "kn", "forecast blend"),
    "harbor.py": ("BerthSchedule", "berth clearance", "berth", "m", "pilotage rules"),
}

folder = sys.argv[1]
random.seed(11)
for filename, (cls, quantity, unit, measure, source) in MODULES.items():
    lines = [
        f'"""{quantity.capitalize()} helpers for the tidewatch service."""',
        "from dataclasses import dataclass",
        "from datetime import datetime",
        "import math",
        "",
        "@dataclass",
        f"class {cls}:",
        f'    """One {unit} and the {source} its readings come from."""',
        "    id: int",
        "    name: str",
        "    latitude: float",
        "    longitude: float",
        "",
    ]
    n = 0
    while sum(len(line) + 1 for line in lines) < 45_000:
        n += 1
        amplitude = f"{random.randint(2, 9)}.{random.randint(10, 99)}"
        lines += [
            "",
            f"def {quantity.replace(' ', '_')}_at_{unit}_{n:03d}(when: datetime) -> float:",
            f'    """{quantity.capitalize()} at {unit} {n}, in {measure}, from its {source}."""',
            f"    amplitude, phase = {amplitude}, math.radians({random.randint(0, 359)})",
            "    hours = (when - datetime(2026, 1, 1)).total_seconds() / 3600",
            "    return round(amplitude * math.cos(0.5059 * hours - phase), 3)",
        ]
    with open(os.path.join(folder, filename), "w") as f:
        f.write("\n".join(lines) + "\n")
