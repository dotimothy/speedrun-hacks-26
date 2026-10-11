"""Feed a live phone call into a review session while it is still happening.

Until now a Dial call was analysed once, after it ended. The dashboard sat
empty through the conversation and a result appeared when the caller hung up,
which is the opposite of what this product claims: updates while the call is
in progress, for an operator to review as they go.

``CallLoop`` already asks ``choose_reply`` every few seconds of caller audio
and does not care what that callback does before answering. So that one hook
does both jobs here -- transcribe the new audio, append it to the call's
session so the review queue fills live, and only then decide whether to speak.
One pass, one transcript, no second pipeline running beside the first.

Two deliberate choices about what gets spoken:

* **Silence during a safety flag.** If a weapon or a trapped caller has been
  flagged, this returns ``None`` every tick. A synthetic voice talking over
  the worst moment of a call is the failure worth designing out, and it is
  worth more than the question it would have asked.
* **Approved phrases only.** The return value is a phrase id. There is no path
  from here to free text, so nothing unreviewed can reach a caller.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

from ..asr import whisper_engine
from ..autoreply import FIELD_PHRASES, SUPPORTED_LANGUAGES
from ..config import settings
from ..confidence import resolve as resolve_confidence
from ..decision.registry import get_engine
from ..decision.rules import RuleEngine
from ..tts import APPROVED
from .diffing import build_suggestions
from .models import Diagnostics, Segment, Speaker
from .store import SessionNotFound, store

log = logging.getLogger("dispatchiq.dial.bridge")

rule_engine = RuleEngine()

# Audio shorter than this is not worth a Whisper pass: it is usually a breath
# or a syllable, and short clips are where hallucinated filler comes from.
MIN_CHUNK_SECONDS = 1.8

# Safety flags that stop the agent speaking entirely.
SILENCING_FLAGS = {"weapon_mentioned", "caller_may_be_trapped"}


@dataclass
class DialBridge:
    """Per-call state: where we have read up to, and what we have already said."""

    call_id: str
    whisper_size: str = "small"
    _offset: int = 0
    _spoken: list[str] = field(default_factory=list)
    _busy: bool = False
    _started: float = field(default_factory=time.perf_counter)

    # ------------------------------------------------------------------
    def ensure_session(self) -> None:
        if not store.exists(self.call_id):
            store.create(self.call_id)
            log.info("live session opened for Dial call %s", self.call_id)

    def _pcm_to_wav(self, pcm: bytes) -> str:
        handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(handle, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        handle.close()
        return handle.name

    # ------------------------------------------------------------------
    async def choose_reply(self, session) -> str | None:
        """Called every few seconds of caller audio by ``CallLoop``."""
        # A tick that arrives while the previous one is still transcribing is
        # dropped rather than queued. A backlog would put older answers on
        # screen after newer ones, and the caller has moved on anyway.
        if self._busy:
            return None

        pcm = bytes(session.pcm16k[self._offset :])
        if len(pcm) / 2 / 16000 < MIN_CHUNK_SECONDS:
            return None

        self._busy = True
        self._offset = len(session.pcm16k)
        try:
            return await asyncio.to_thread(self._process_chunk, pcm)
        except Exception as exc:  # a failed tick must not drop the call
            log.warning("dial bridge tick failed for %s: %s", self.call_id, exc)
            return None
        finally:
            self._busy = False

    # ------------------------------------------------------------------
    def _process_chunk(self, pcm: bytes) -> str | None:
        self.ensure_session()
        path = self._pcm_to_wav(pcm)
        started = time.perf_counter()
        try:
            original, english = whisper_engine.transcribe_and_translate(
                path, self.whisper_size
            )
        finally:
            Path(path).unlink(missing_ok=True)

        if original is None or not original.text.strip():
            # Silence, or noise. Saying nothing was heard is the honest
            # outcome; inventing a segment from it is not.
            return None

        asr_ms = (time.perf_counter() - started) * 1000
        snapshot = store.get(self.call_id)
        elapsed_ms = round((time.perf_counter() - self._started) * 1000)

        segment = Segment(
            id=store.next_id(self.call_id, "seg"),
            index=len(snapshot.segments),
            speaker=Speaker.CALLER,
            original_text=original.text.strip(),
            language=original.language,
            translation=(
                english.text.strip()
                if english is not None and english.text.strip() != original.text.strip()
                else None
            ),
            start_ms=max(0, elapsed_ms - round(len(pcm) / 2 / 16000 * 1000)),
            end_ms=elapsed_ms,
            final=True,
        )

        # Inference over the caller's words so far, as on the typed path:
        # a three-second slice on its own says very little.
        caller_text = " ".join(
            s.original_text for s in [*snapshot.segments, segment]
            if s.speaker is Speaker.CALLER
        )
        caller_english = " ".join(
            (s.translation or s.original_text) for s in [*snapshot.segments, segment]
            if s.speaker is Speaker.CALLER
        )

        engine = get_engine()
        decision_started = time.perf_counter()
        engine_result = engine.extract(caller_text, caller_english, original.language)
        rule_result = rule_engine.extract(caller_text, caller_english, original.language)
        resolution = resolve_confidence(
            engine_result, rule_result, caller_text, caller_english,
            asr_confidence=original.word_confidence,
        )
        decision_ms = (time.perf_counter() - decision_started) * 1000

        outcome = build_suggestions(
            accepted=snapshot.accepted_record,
            pending=snapshot.pending,
            predictions=resolution.predictions,
            segment=segment,
            base_revision=snapshot.revision,
            next_id=lambda prefix: store.next_id(self.call_id, prefix),
        )

        store.commit_append(
            self.call_id,
            segment,
            outcome.suggestions,
            outcome.flags,
            outcome.unresolved,
            superseded=outcome.superseded,
            corroborated=outcome.corroborated,
            detected_language=original.language,
            language_probability=original.language_probability,
            diagnostics=Diagnostics(
                last_asr_ms=round(asr_ms, 1),
                last_decision_ms=round(decision_ms, 1),
                last_total_ms=round((time.perf_counter() - started) * 1000, 1),
                engine={
                    "decision": engine_result.engine,
                    "decision_model": engine_result.model,
                    "transport": "dial",
                    "inference_location": engine.describe().get(
                        "inference_location", "unknown"
                    ),
                },
                calibration_warning=settings.CALIBRATION_WARNING,
            ),
        )

        log.info(
            "call %s: %r (asr %.0fms, decision %.0fms, %d pending)",
            self.call_id, segment.original_text[:60], asr_ms, decision_ms,
            len(store.get(self.call_id).pending),
        )
        return self._pick_phrase()

    # ------------------------------------------------------------------
    def _pick_phrase(self) -> str | None:
        """The next approved question, or None to stay quiet."""
        snapshot = store.get(self.call_id)

        if any(flag.kind.value in SILENCING_FLAGS for flag in snapshot.open_flags):
            # Hand over rather than talk. See the module docstring.
            return None

        language = (snapshot.detected_language or "en")[:2]
        if language not in SUPPORTED_LANGUAGES:
            return None

        known = {
            name for name, value in snapshot.accepted_record.items() if value.value
        }
        # A pending suggestion means the field has been heard and is waiting
        # for a human, so asking about it again would be talking past them.
        known |= {s.field for s in snapshot.pending}

        for field_name, base in FIELD_PHRASES:
            if field_name in known:
                continue
            phrase_id = f"{base}_{language}"
            if phrase_id in self._spoken or phrase_id not in APPROVED:
                continue
            self._spoken.append(phrase_id)
            return phrase_id
        return None


_bridges: dict[str, DialBridge] = {}


def bridge_for(call_id: str) -> DialBridge:
    bridge = _bridges.get(call_id)
    if bridge is None:
        bridge = DialBridge(call_id=call_id)
        bridge.ensure_session()
        _bridges[call_id] = bridge
    return bridge


def close_bridge(call_id: str) -> None:
    _bridges.pop(call_id, None)


def active_call_ids() -> list[str]:
    return list(_bridges)


def is_live(call_id: str) -> bool:
    try:
        store.get(call_id)
    except SessionNotFound:
        return False
    return call_id in _bridges
