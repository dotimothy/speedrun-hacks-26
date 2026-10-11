"""Per-stage timing. The demo claims a latency breakdown, so measure it."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator


class StageTimer:
    """Accumulates elapsed milliseconds per named stage.

    Stages are additive, so a stage that is skipped stays at 0.0 rather than
    being absent -- a zero is a measurement, not a gap.
    """

    def __init__(self) -> None:
        self._stages: dict[str, float] = {}
        self._wall_start = time.perf_counter()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - start) * 1000.0
            self._stages[name] = self._stages.get(name, 0.0) + elapsed

    def add(self, name: str, ms: float) -> None:
        self._stages[name] = self._stages.get(name, 0.0) + ms

    def get(self, name: str) -> float:
        return round(self._stages.get(name, 0.0), 1)

    @property
    def server_total_ms(self) -> float:
        """Whole time spent inside the request handler, not a sum of stages.

        Reporting the real wall clock means any time we failed to instrument
        shows up as a discrepancy instead of vanishing.
        """
        return round((time.perf_counter() - self._wall_start) * 1000.0, 1)
