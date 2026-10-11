"""Laya: the primary decision engine.

Laya answers typed questions about a text in a single non-autoregressive
forward pass. It does not generate language, it scores options. Measured on
this machine: 35-45 ms warm for the full 11-question set, against 2.3-10.5 s
for a generative model asked the same thing. That gap is what makes a
per-utterance decision loop plausible at all.

Two properties of it drive the code below.

**It routes by language.** ``Router`` detects the script and language of the
state and picks a checkpoint: English Latin text goes to the English
checkpoint, Spanish to the multilingual one. Whisper hands us an English
translation for free, so the same questions can be asked twice -- once in the
caller's own words, once in translation -- and the answers compared.

**Its probabilities are not calibrated.** Loading a checkpoint emits a warning
that the shipped temperatures are invalid for some option counts, and Laya's
own documentation says the checkpoints are over-confident. A measured example
from this repo's fixtures: a Spanish kitchen-fire call returned ``gas_leak`` at
0.956 from the multilingual checkpoint, while the English translation of the
same sentence returned ``structure_fire`` at 0.867. One of those is confidently
wrong, and nothing in either number says which.

So this engine runs both passes and never silently picks a winner. Where they
disagree the field is flagged, penalised, and both values are put in front of
the dispatcher. Agreement between two checkpoints reading two renderings of the
same sentence is a far better signal than either one's self-reported
probability.
"""

from __future__ import annotations

import logging
import time

from ..config import settings
from .base import EngineResult, FieldAnswer
from .questions import ALL_QUESTIONS

log = logging.getLogger("dispatchiq.laya")


class LayaEngine:
    name = "laya"
    # Runs on this machine.
    is_local = True

    def __init__(self) -> None:
        self._router = None
        self._load_error: str | None = None
        self._device: str | None = None

    @staticmethod
    def _resolve_device() -> str:
        """Choose the accelerator explicitly rather than leaving it to chance.

        Laya autodetects in the order CUDA, MPS, XPU, CPU, which would land in
        the right place anyway -- but naming it means ``/api/health`` can state
        what is actually in use. On an Apple Silicon Mac this is ``mps``, and
        it matters there: Whisper is stuck on the CPU because CTranslate2 has
        no Metal backend, so the decision stage is the part that can still use
        the GPU.
        """
        if settings.laya_device:
            return settings.laya_device
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if torch.backends.mps.is_available():
                return "mps"
        except Exception:  # pragma: no cover - probe guard
            pass
        return "cpu"

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def _get_router(self):
        if self._router is not None or self._load_error is not None:
            return self._router
        try:
            import laya  # imported lazily so the API still starts without it
        except Exception as exc:  # pragma: no cover - import guard
            self._load_error = f"laya is not installed: {exc}"
            log.warning(self._load_error)
            return None
        try:
            self._device = self._resolve_device()
            self._router = laya.Router(
                default=settings.laya_default_checkpoint,
                device=self._device,
                max_loaded=settings.laya_max_loaded,
                preload=settings.laya_preload,
            )
        except Exception as exc:
            self._load_error = f"laya router failed to load: {exc}"
            log.warning(self._load_error)
            return None
        return self._router

    def ready(self) -> bool:
        return self._get_router() is not None

    @property
    def load_error(self) -> str | None:
        self._get_router()
        return self._load_error

    @property
    def device(self) -> str:
        return self._device or self._resolve_device()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "model": settings.laya_default_checkpoint,
            "device": self.device,
            "is_local": True,
            "inference_location": "this machine",
        }

    def warmup(self) -> None:
        """Pay the checkpoint download and load cost before the first call."""
        router = self._get_router()
        if router is None:
            return
        try:
            router.predict(
                {"transcript": "warmup"}, {"w": ALL_QUESTIONS["weapons_present"]}
            )
        except Exception as exc:  # pragma: no cover
            log.warning("laya warmup failed: %s", exc)

    # ------------------------------------------------------------------
    # inference
    # ------------------------------------------------------------------
    def _one_pass(self, text: str, decided_on: str) -> tuple[dict[str, FieldAnswer], dict]:
        router = self._get_router()
        if router is None or not text.strip():
            return {}, {}

        result = router.predict({"transcript": text}, ALL_QUESTIONS)
        answers: dict[str, FieldAnswer] = {}

        for qid, raw in (result.get("answers") or {}).items():
            qtype = raw.get("type")

            if qtype == "choice":
                answers[qid] = FieldAnswer(
                    label=qid,
                    value=raw.get("choice"),
                    probability=raw.get("answer_confidence"),
                    probabilities=raw.get("probabilities"),
                    entropy_confidence=raw.get("confidence"),
                    decided_on=decided_on,
                )

            elif qtype == "noul":
                # noul is P(the statement is true). The raw probability is kept
                # as the value and the threshold policy in confidence.py turns
                # it into yes / no / unknown. This engine does not get to decide
                # that a 0.45 means "no".
                p_true = float(raw.get("noul", 0.0))
                answers[qid] = FieldAnswer(
                    label=qid,
                    value=p_true,
                    probability=raw.get("answer_confidence", max(p_true, 1.0 - p_true)),
                    probabilities={"true": p_true, "false": 1.0 - p_true},
                    entropy_confidence=raw.get("confidence"),
                    decided_on=decided_on,
                )

            elif qtype == "score":  # unused today, handled so it cannot vanish
                answers[qid] = FieldAnswer(
                    label=qid,
                    value=raw.get("score"),
                    probability=raw.get("answer_confidence"),
                    probabilities=raw.get("probabilities"),
                    entropy_confidence=raw.get("confidence"),
                    decided_on=decided_on,
                )

        return answers, result.get("routing") or {}

    def extract(self, original: str, english: str, language: str | None) -> EngineResult:
        started = time.perf_counter()
        notes: list[str] = []

        primary, routing = self._one_pass(original, "original")

        # The cross-check only means something if it is a genuinely different
        # rendering of the same words. For an English call both passes would
        # route to the same checkpoint with the same text, so skip it and say
        # so rather than reporting a fake second opinion.
        cross: dict[str, FieldAnswer] = {}
        cross_routing: dict = {}
        if english.strip() and english.strip() != original.strip():
            cross, cross_routing = self._one_pass(english, "english")
            if cross:
                notes.append(
                    "Cross-checked against Whisper's English translation on a "
                    "second checkpoint; per-field disagreements are flagged."
                )
        else:
            notes.append(
                "Call was already English, so there is no second rendering to "
                "cross-check against. Scores rest on a single checkpoint."
            )

        if self._load_error:
            notes.append(self._load_error)

        return EngineResult(
            answers=primary,
            cross=cross,
            engine=self.name,
            model=str(routing.get("model") or settings.laya_default_checkpoint),
            routing={"primary": routing, "cross": cross_routing},
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            notes=notes,
        )
