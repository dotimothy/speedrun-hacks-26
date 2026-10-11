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
    """The typed answer, and nothing else.

    An earlier version also asked for a 0-1 confidence. It was removed on
    purpose: the only generative model measured on this project returned
    exactly 1.0 for every field, every time, so the number carried no
    information and invited a client to render it as a percentage. Asking a
    model how sure it is produces a sentence about confidence, not a
    measurement of it.

    Abstention is kept as a value instead, which a model *can* answer
    honestly: every question may be answered "unknown", and that answer
    survives into the record as "unknown" rather than being inferred from a
    low number. Support is then derived the same way it is for every other
    engine -- from agreement with the rule extractor and from evidence in the
    transcript.
    """
    properties: dict[str, dict] = {}
    for name, options in _CHOICE_FIELDS.items():
        properties[name] = {
            "type": "string",
            "enum": list(dict.fromkeys([*options, "unknown"])),
            "description": ALL_QUESTIONS[name]["instructions"] + " Choices: " + json.dumps(ALL_QUESTIONS[name]["criteria"]),
        }
    for name in _NOUL_FIELDS:
        properties[name] = {"type": "string", "enum": ["yes", "no", "unknown"]}
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
            value = payload.get(name)
            # "unknown" is an answer, not a failure, and it stops here rather
            # than becoming a low-confidence guess downstream.
            if value not in options:
                continue
            answers[name] = FieldAnswer(
                label=name,
                value=value,
                # No probability: this engine is not asked for one. The
                # confidence layer applies a neutral prior and moves it on
                # corroboration, exactly as it would for any typed answer with
                # no number attached.
                probability=None,
                decided_on="english",
            )

        # The tri-state answers round-trip through the probability band the
        # rest of the pipeline thresholds on: yes -> 1.0, no -> 0.0, and
        # unknown -> 0.5, which lands in the abstention band by construction.
        _BAND = {"yes": 1.0, "no": 0.0, "unknown": 0.5}
        for name in _NOUL_FIELDS:
            spoken = payload.get(name)
            if spoken not in _BAND:
                continue
            answers[name] = FieldAnswer(
                label=name,
                value=_BAND[spoken],
                probability=None,
                decided_on="english",
                notes=[f"typed answer: {spoken}"],
            )

        return EngineResult(
            answers=answers,
            engine=self.name,
            model=settings.openai_model,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            notes=notes,
        )
