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

# Safety flags that stop the agent speaking entirely.
SILENCING_FLAGS = {"weapon_mentioned", "caller_may_be_trapped"}

# How long before a still-unanswered question may be asked again, in decision
# ticks (a tick is settings.dial_decide_every_s of caller audio), and how many
# times in total. Enough of a pause that it reads as patience rather than a
# loop, and a low enough ceiling that it stops rather than nags.
REASK_COOLDOWN_TICKS = 4
REASK_LIMIT = 2

# The holding phrase, for when there is nothing left to ask. Rare, because its
# job is to show the line is open, not to fill every pause.
HOLDING_COOLDOWN_TICKS = 6
HOLDING_LIMIT = 4


@dataclass
class DialBridge:
    """Per-call state: where we have read up to, and what we have already said."""

    call_id: str
    whisper_size: str = field(default_factory=lambda: settings.dial_whisper_size)
    _offset: int = 0
    # phrase id -> the tick it was last spoken on, and how many times.
    _spoken: dict[str, tuple[int, int]] = field(default_factory=dict)
    _tick: int = 0
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
        if len(pcm) / 2 / 16000 < settings.dial_min_chunk_s:
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
    def _say(self, phrase_id: str) -> str:
        asked_at, times = self._spoken.get(phrase_id, (0, 0))
        self._spoken[phrase_id] = (self._tick, times + 1)
        return phrase_id

    def _may_say(self, phrase_id: str, *, cooldown: int, limit: int) -> bool:
        if phrase_id not in APPROVED:
            return False
        asked_at, times = self._spoken.get(phrase_id, (-999, 0))
        return times < limit and (self._tick - asked_at) >= cooldown

    def _pick_phrase(self) -> str | None:
        """The next thing to say, or None to stay quiet.

        The first version of this walked the question list once and returned
        None forever after the last one -- so a caller who got as far as "Are
        you safe right now?" was answered with permanent silence for the rest
        of the call. Two things were wrong with it, and both are fixed here.

        A question was asked once ever, even if the caller never answered it.
        A field that is still unknown is worth asking about again after a
        pause, which is what a person would do; ``REASK_COOLDOWN_TICKS`` and
        ``REASK_LIMIT`` keep that from becoming nagging.

        And running out of questions is not a reason to go mute. A line that
        has gone silent sounds like a dropped call, so the fallback is a
        holding phrase rather than nothing.

        The one case that still produces silence is the one that should: an
        open weapon or trapped-caller flag.
        """
        snapshot = store.get(self.call_id)
        self._tick += 1

        if any(flag.kind.value in SILENCING_FLAGS for flag in snapshot.open_flags):
            # Hand over rather than talk. See the module docstring.
            return None

        language = (snapshot.detected_language or "en")[:2]
        if language not in SUPPORTED_LANGUAGES:
            return None

        # A field counts as handled once it is accepted, or once a suggestion
        # for it is waiting on a human -- asking again would talk past them.
        handled = {
            name for name, value in snapshot.accepted_record.items() if value.value
        }
        handled |= {s.field for s in snapshot.pending}

        # First pass: anything never asked. Second: anything still unknown
        # whose cooldown has elapsed.
        for cooldown, limit in ((0, 1), (REASK_COOLDOWN_TICKS, REASK_LIMIT)):
            for field_name, base in FIELD_PHRASES:
                if field_name in handled:
                    continue
                phrase_id = f"{base}_{language}"
                if self._may_say(phrase_id, cooldown=cooldown, limit=limit):
                    return self._say(phrase_id)

        # Nothing to ask. Say something anyway, occasionally: the caller
        # should be able to tell the line is still open.
        holding = f"stay_on_line_{language}"
        if self._may_say(holding, cooldown=HOLDING_COOLDOWN_TICKS, limit=HOLDING_LIMIT):
            return self._say(holding)
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
