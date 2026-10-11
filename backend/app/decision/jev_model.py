"""Jev (TypeSafe System-One) as a decision engine.

Same ``DecisionEngine`` protocol as Laya, the rule extractor and OpenAI, so
the session layer, the diffing rules and the UI cannot tell which one
answered.

**The package is not installed here, on purpose.** ``system-one-adapter``
0.2.1 exists on PyPI, but installing it would also upgrade ``anyio``, ``h11``,
``idna``, ``packaging`` and ``typing_extensions`` -- the transitive
dependencies underneath FastAPI, Starlette and uvicorn. Trading a working
demo for an engine nobody has asked to run yet is a bad trade during a
hackathon, so the import is lazy: this engine reports itself unavailable, and
becomes available the moment somebody installs the package deliberately.

    pip install system-one-adapter        # note what it upgrades first

What *is* available today, with no new dependency, is Jev's **confidence
formula**. Laya ships ``jev_confidence`` precisely because the two products
compute confidence differently, and its own documentation is blunt about what
that means:

    Laya's `confidence` is normalized entropy, and Jev's is a different
    formula, so a threshold carried over from Jev selects about twice as much
    on Laya.

That is a real trap for a team moving between the two, and it is the reason
``jev_confidence`` is exposed here as a reported metric on every Laya answer
rather than buried. A threshold is only meaningful against the formula it was
tuned on.
"""

from __future__ import annotations

import logging
import time

from ..config import settings
from .base import EngineResult, FieldAnswer
from .questions import ALL_QUESTIONS

log = logging.getLogger("dispatchiq.jev")


def jev_confidence_for(answer: dict) -> float | None:
    """Jev's confidence for a Laya answer, or None.

    Delegates to Laya's own implementation rather than reimplementing the
    formula, so the number cannot drift from what Jev would actually report.
    Returns None for a noul answer and for malformed probabilities -- Laya is
    deliberate about that, so a caller filtering on this number never filters
    on an invented one.
    """
    try:
        from laya import jev_confidence

        return jev_confidence(answer)
    except Exception:  # pragma: no cover - laya absent or answer malformed
        return None


class JevEngine:
    name = "jev"
    # The adapter talks to a TypeSafe endpoint. Locality is therefore a
    # function of where that endpoint is: a self-hosted deployment is local,
    # the hosted service is not. Default to the cautious answer and let
    # configuration say otherwise, because getting this wrong makes the
    # project's main claim silently false.
    is_local = False

    def __init__(self) -> None:
        self._client = None
        self._load_error: str | None = None

    def _get_client(self):
        if self._client is not None or self._load_error is not None:
            return self._client
        try:
            from system_one_adapter import SystemOneAdapter  # type: ignore
        except Exception as exc:
            self._load_error = (
                "system-one-adapter is not installed. Installing it also "
                "upgrades anyio, h11, idna, packaging and typing_extensions, "
                "which FastAPI and uvicorn sit on -- check that before "
                f"running `pip install system-one-adapter` ({exc})"
            )
            return None

        if not settings.jev_endpoint and not settings.jev_api_key:
            self._load_error = "no DISPATCHIQ_JEV_ENDPOINT or DISPATCHIQ_JEV_API_KEY set"
            return None

        try:
            kwargs = {}
            if settings.jev_endpoint:
                kwargs["base_url"] = settings.jev_endpoint
            if settings.jev_api_key:
                kwargs["api_key"] = settings.jev_api_key
            self._client = SystemOneAdapter(**kwargs)
        except Exception as exc:  # pragma: no cover - credential guard
            self._load_error = f"jev adapter failed to initialise: {exc}"
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
        self._get_client()
        return {
            "name": self.name,
            "model": settings.jev_model or "system-one",
            "is_local": settings.jev_is_local,
            "inference_location": (
                "self-hosted Jev endpoint"
                if settings.jev_is_local
                else "TypeSafe System-One (off this machine)"
            ),
            "error": self._load_error,
            "note": (
                "Jev's confidence formula differs from Laya's normalized "
                "entropy; a threshold tuned on one selects roughly twice as "
                "much on the other. See jev_confidence_for()."
            ),
        }

    def extract(self, original: str, english: str, language: str | None) -> EngineResult:
        started = time.perf_counter()
        client = self._get_client()
        if client is None:
            return EngineResult(
                answers={},
                engine=self.name,
                model=settings.jev_model or "system-one",
                notes=[self._load_error or "Jev engine unavailable"],
            )

        transcript = original or english
        try:
            # The adapter takes the same shape of typed questions Laya does,
            # which is why one question set serves both.
            result = client.predict({"transcript": transcript}, ALL_QUESTIONS)
        except Exception as exc:
            log.warning("jev call failed: %s", exc)
            return EngineResult(
                answers={}, engine=self.name, model=settings.jev_model or "system-one",
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                notes=[f"Jev call failed: {exc}"],
            )

        answers: dict[str, FieldAnswer] = {}
        for qid, raw in (result.get("answers") or {}).items():
            qtype = raw.get("type")
            if qtype == "choice":
                answers[qid] = FieldAnswer(
                    label=qid,
                    value=raw.get("choice"),
                    probability=raw.get("confidence"),
                    probabilities=raw.get("probabilities"),
                    decided_on="original",
                )
            elif qtype == "noul":
                p_true = float(raw.get("noul", 0.0))
                answers[qid] = FieldAnswer(
                    label=qid,
                    value=p_true,
                    probability=max(p_true, 1.0 - p_true),
                    probabilities={"true": p_true, "false": 1.0 - p_true},
                    decided_on="original",
                )

        return EngineResult(
            answers=answers,
            engine=self.name,
            model=settings.jev_model or "system-one",
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            notes=[
                "Confidence here is Jev's formula, not Laya's normalized "
                "entropy. Do not carry a threshold across unchanged."
            ],
        )
