"""OpenAI as a decision engine.

Implements the same ``DecisionEngine`` protocol as Laya and the rule
extractor, so it drops in without the session layer, the diffing rules or the
UI knowing which one answered.

**Read this before switching it on.** Every other engine in this project runs
on the machine in front of you. This one sends the caller's words to a third
party over the internet. That is a different product with different promises:

* The README, ``plan.md`` and the pitch all say "without cloud inference".
  With this engine active, that sentence is false, and the demo script needs
  changing rather than the claim being quietly dropped.
* Emergency-call audio is about as sensitive as text gets. These are synthetic
  scenarios today, which is the only reason this is reasonable at all. It must
  not be pointed at anything real.
* It adds a network round trip to a path measured in tens of milliseconds, and
  a dependency on someone else's availability during a live call.

So it is opt-in, never the default, and it reports ``is_local = False`` so
``/api/health`` and every response can say plainly where inference happened.
A system that silently changes where a caller's words go is worse than one
that cannot do it at all.
"""

from __future__ import annotations

import json
import logging
import os
import time

from ..config import settings
from .base import EngineResult, FieldAnswer
from .questions import ALL_QUESTIONS

log = logging.getLogger("dispatchiq.openai")

# The same questions Laya answers, expressed as a JSON schema. Keeping one
# question set means the two engines can be compared on identical input --
# which is the only way to tell whether the cloud is actually buying anything.
_CHOICE_FIELDS = {
    name: tuple(spec["criteria"].keys())
    for name, spec in ALL_QUESTIONS.items()
    if spec["type"] == "choice"
}
_NOUL_FIELDS = tuple(
    name for name, spec in ALL_QUESTIONS.items() if spec["type"] == "noul"
)


def _schema() -> dict:
    properties: dict[str, dict] = {}
    for name, options in _CHOICE_FIELDS.items():
        properties[name] = {
            "type": "object",
            "properties": {
                "value": {"type": "string", "enum": list(options)},
                # Asked for, but treated as the model's claim rather than a
                # measurement -- see the note in `extract`.
                "probability": {"type": "number"},
            },
            "required": ["value", "probability"],
            "additionalProperties": False,
        }
    for name in _NOUL_FIELDS:
        properties[name] = {
            "type": "object",
            "properties": {"probability": {"type": "number"}},
            "required": ["probability"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_INSTRUCTIONS = (
    "You are reading a transcript of a simulated emergency call and answering "
    "typed questions about it. Answer only from what the transcript says. "
    "Where the transcript does not establish something, give it a probability "
    "near zero rather than guessing: an unanswered question is a useful "
    "answer here and a fabricated one is harmful. Do not infer injuries, "
    "weapons or head-counts that were not stated."
)


class OpenAIEngine:
    name = "openai"
    # The whole reason this flag exists on the protocol.
    is_local = False

    def __init__(self) -> None:
        self._client = None
        self._load_error: str | None = None

    def _api_key(self) -> str | None:
        return settings.openai_api_key or os.environ.get("OPENAI_API_KEY")

    def _get_client(self):
        if self._client is not None or self._load_error is not None:
            return self._client
        key = self._api_key()
        if not key:
            self._load_error = (
                "no OPENAI_API_KEY set; the OpenAI engine stays off and the "
                "local engines are used instead"
            )
            return None
        try:
            from openai import OpenAI

            self._client = OpenAI(api_key=key, timeout=settings.openai_timeout_s)
        except Exception as exc:  # pragma: no cover - import/credential guard
            self._load_error = f"openai client unavailable: {exc}"
            log.warning(self._load_error)
            return None
        return self._client

    def ready(self) -> bool:
        return self._get_client() is not None

    @property
    def load_error(self) -> str | None:
        self._get_client()
        return self._load_error

    def describe(self) -> dict:
        return {
            "name": self.name,
            "model": settings.openai_model,
            "is_local": False,
            "inference_location": "OpenAI API (off this machine)",
            "warning": (
                "Transcript text is sent to a third party. The project's "
                "local-inference claim does not hold while this engine is active."
            ),
        }

    def extract(self, original: str, english: str, language: str | None) -> EngineResult:
        started = time.perf_counter()
        client = self._get_client()
        if client is None:
            return EngineResult(
                answers={}, engine=self.name, model=settings.openai_model,
                notes=[self._load_error or "OpenAI engine unavailable"],
            )

        # Send one rendering, not both. Two passes would double the data
        # leaving the machine for a cross-check this engine cannot honestly
        # provide anyway: the same model reading its own translation is not an
        # independent opinion, where Laya's two checkpoints at least are.
        transcript = english or original
        notes = [
            "Inference ran on the OpenAI API, not on this machine.",
            "Single pass: no cross-language check, because the same model "
            "reading its own translation is not an independent second opinion.",
        ]

        try:
            response = client.chat.completions.create(
                model=settings.openai_model,
                temperature=0,
                messages=[
                    {"role": "system", "content": _INSTRUCTIONS},
                    {"role": "user", "content": f"Transcript:\n{transcript}"},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "incident_fields",
                        "strict": True,
                        "schema": _schema(),
                    },
                },
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:
            log.warning("openai call failed: %s", exc)
            return EngineResult(
                answers={}, engine=self.name, model=settings.openai_model,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                notes=[f"OpenAI call failed: {exc}"],
            )

        answers: dict[str, FieldAnswer] = {}
        for name, options in _CHOICE_FIELDS.items():
            entry = payload.get(name) or {}
            value = entry.get("value")
            if value not in options:
                continue
            answers[name] = FieldAnswer(
                label=name,
                value=value,
                # A self-reported number. Measured on this project's other
                # generative model: it returned exactly 1.0 every time. The
                # confidence layer treats it as a claim to be corroborated,
                # not as a probability, which is why it is safe to pass on.
                probability=float(entry.get("probability") or 0.0),
                decided_on="english",
            )
        for name in _NOUL_FIELDS:
            entry = payload.get(name) or {}
            p_true = float(entry.get("probability") or 0.0)
            answers[name] = FieldAnswer(
                label=name,
                value=p_true,
                probability=max(p_true, 1.0 - p_true),
                probabilities={"true": p_true, "false": 1.0 - p_true},
                decided_on="english",
            )

        return EngineResult(
            answers=answers,
            engine=self.name,
            model=settings.openai_model,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            notes=notes,
        )
