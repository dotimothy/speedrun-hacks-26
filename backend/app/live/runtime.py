"""Settings an operator can change while the system is running.

The settings menu used to be a property of one browser tab: it picked a
Whisper size and a decision engine, and sent them along with that tab's own
requests. A phone call does not come from a browser tab. It arrives on a
socket with nobody's preferences attached, so it ran on the configured
defaults whatever the menu said -- the control was on screen and was not
connected to the thing being demonstrated.

So the choice lives here, on the server, where both paths can read it. The
menu writes it; a phone call and a typed utterance both pick it up.

Two things stay out of reach on purpose. A cloud engine can only be selected
if the deployment allows cloud inference at all -- that is a configuration
decision, not a menu item. And the abstention thresholds are not here: they
change what every score means, so they are not something to nudge mid-call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import settings
from ..decision.registry import available


class InvalidSetting(ValueError):
    """The requested value is not one this deployment offers."""


@dataclass
class Runtime:
    whisper_size: str = field(default_factory=lambda: settings.dial_whisper_size)
    decision_engine: str = field(default_factory=lambda: settings.decision_engine)

    def as_dict(self) -> dict:
        return {
            "whisper_size": self.whisper_size,
            "decision_engine": self.decision_engine,
        }

    def update(
        self,
        *,
        whisper_size: str | None = None,
        decision_engine: str | None = None,
    ) -> dict:
        # Validate everything before changing anything, so a bad engine name
        # cannot leave a half-applied update behind.
        if whisper_size is not None and whisper_size not in settings.whisper_sizes:
            raise InvalidSetting(
                f"unknown Whisper size {whisper_size!r}; "
                f"choose one of {', '.join(settings.whisper_sizes)}"
            )
        if decision_engine is not None and decision_engine not in available():
            raise InvalidSetting(
                f"decision engine {decision_engine!r} is not available here; "
                f"choose one of {', '.join(available())}"
            )
        if whisper_size is not None:
            self.whisper_size = whisper_size
        if decision_engine is not None:
            self.decision_engine = decision_engine
        return self.as_dict()


runtime = Runtime()
