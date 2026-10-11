"""Controlled comparison: filling the form unaided vs. correcting a draft.

This is the only place the project produces a time-saving number, and it is
deliberately modest about it. It measures a handful of operators on synthetic
calls in a quiet room. That is not evidence about real dispatch work, and the
output says so in the file itself so the caveat travels with the number.

    python scripts/timing_study.py --operator alex
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "docs" / "timing_study.json"

FIELDS = [
    "incident type", "location", "callback number", "injuries",
    "persons involved", "hazards", "weapon present", "caller safe",
]


def timed_prompt(heading: str, instructions: str) -> float:
    print("\n" + "=" * 68)
    print(heading)
    print("=" * 68)
    print(instructions)
    input("\npress Enter to START the timer, then do the task... ")
    started = time.perf_counter()
    input("press Enter the moment you are finished... ")
    return time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operator", required=True, help="initials or a nickname, not a real name")
    parser.add_argument("--scenario", default="kitchen_fire_en")
    args = parser.parse_args()

    audio = REPO / "fixtures" / "audio" / f"{args.scenario}.wav"
    print(f"Scenario: {args.scenario}")
    print(f"Audio:    {audio}")
    print("\nPlay the WAV yourself; this script only holds the stopwatch.")

    unaided = timed_prompt(
        "A — unaided",
        "Listen to the call and fill in all eight fields on paper or in a blank\n"
        "form, from scratch:\n  " + "\n  ".join(FIELDS),
    )
    assisted = timed_prompt(
        "B — assisted",
        "Open the dashboard, run the same scenario, then read the pre-filled\n"
        "draft, correct anything wrong, and confirm. Stop when you would be\n"
        "willing to submit it.",
    )

    record = {
        "date": date.today().isoformat(),
        "operator": args.operator,
        "scenario": args.scenario,
        "unaided_s": round(unaided, 1),
        "assisted_s": round(assisted, 1),
        "delta_s": round(unaided - assisted, 1),
    }

    existing = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else []
    existing.append(record)
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    print(f"\nunaided {record['unaided_s']}s   assisted {record['assisted_s']}s   "
          f"difference {record['delta_s']}s")

    if len(existing) >= 2:
        unaided_all = [r["unaided_s"] for r in existing]
        assisted_all = [r["assisted_s"] for r in existing]
        print(f"\nacross {len(existing)} runs: median unaided "
              f"{statistics.median(unaided_all):.1f}s, median assisted "
              f"{statistics.median(assisted_all):.1f}s")

    print(
        "\nWhen quoting this, quote it in full: a controlled measurement on\n"
        f"synthetic calls, n={len(existing)} runs, in a quiet room, with operators\n"
        "who already know the scenario. It is not a validated finding about\n"
        "real dispatch work and must not be presented as one."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
