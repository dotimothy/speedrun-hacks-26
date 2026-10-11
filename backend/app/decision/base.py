"""The swap point between DispatchIQ and whatever model answers the questions.

Any engine that satisfies ``DecisionEngine`` can be dropped in without touching
the API, the workflow engine or the UI. Three exist:

* ``laya_model.LayaEngine``     -- primary. Typed decisions, one forward pass.
* ``rules.RuleEngine``          -- deterministic. Cross-check and fallback.
* ``ollama_model.OllamaEngine`` -- generative. Optional, ~100x slower.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class FieldAnswer:
    """One engine's answer about one form field, before any policy is applied.

    Engines report what they found and how sure they are. They do NOT decide
    whether the answer is good enough to show a dispatcher -- that is
    ``confidence.py``'s job, and keeping it out of here is what lets the
    abstention policy be tested without a model.
    """

    label: str
    value: object
    # Raw probability from the engine, untransformed. None for engines that
    # have no notion of one (the rule baseline).
    probability: float | None = None
    probabilities: dict[str, float] | None = None
    # Spread of the whole distribution, where the engine reports it.
    entropy_confidence: float | None = None
    # A quote the engine claims supports the value. Unverified at this point.
    quote: str | None = None
    decided_on: str = "original"
    notes: list[str] = field(default_factory=list)


@dataclass
class EngineResult:
    answers: dict[str, FieldAnswer]
    # The second opinion, keyed the same way. For Laya this is the pass over
    # the other language; for the rule baseline it is empty. Kept separate so
    # the policy layer sees a disagreement rather than a pre-resolved winner.
    cross: dict[str, FieldAnswer] = field(default_factory=dict)
    engine: str = ""
    model: str = ""
    routing: dict[str, object] = field(default_factory=dict)
    latency_ms: float = 0.0
    notes: list[str] = field(default_factory=list)


@runtime_checkable
class DecisionEngine(Protocol):
    name: str

    def ready(self) -> bool:
        """True when the engine can answer without a long cold start."""

    def extract(self, original: str, english: str, language: str | None) -> EngineResult:
        """Answer every field question about one call transcript."""
