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
* **Each question once.** A question comes back only if the caller said
  nothing after it. Once they have answered, a field we still could not fill
  is a gap for the operator to chase, not a question to repeat at them.

The questions themselves come from ``live.followup.follow_up_questions``, the
same plan the operator view shows, so the phone and the screen never disagree
about what is still missing. When there is nothing left on it the agent may
still ask -- every question answered, or asked and replied to -- it plays one
closing phrase, confirming a dispatcher has the record, and the loop hangs up
once that audio has drained. Nothing is dispatched by this prototype, so the
phrase says only that: the record still waits on a human.

Everything the agent says is also written into the session as an operator
utterance, so the dashboard shows both halves of the conversation. An answer
on screen is hard to read without the question it answered.
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
from ..autoreply import SUPPORTED_LANGUAGES
from .followup import follow_up_questions
from ..config import settings
from ..confidence import resolve as resolve_confidence
from ..decision.registry import get_engine
from ..decision.rules import RuleEngine
from ..tts import APPROVED
from .diffing import build_suggestions
from .followup import observed, reviewed_incident
from ..scenarios import known
from .models import Diagnostics, Segment, Speaker
from .store import SessionNotFound, store

log = logging.getLogger("dispatchiq.dial.bridge")

rule_engine = RuleEngine()

# Safety flags that stop the agent speaking entirely.
SILENCING_FLAGS = {"weapon_mentioned", "caller_may_be_trapped"}

# How long before a question may be asked again, in decision ticks (a tick is
# settings.dial_decide_every_s of caller audio), and how many times in total.
#
# A question only comes back if the caller has said nothing at all since it
# was asked -- a silence is worth repeating into, a reply is not. If they
# answered and the extractor still could not fill the field, that is a gap for
# the operator to see in the review queue, not a question to ask twice. Being
# asked the same thing again when you have already answered it is its own kind
# of failure on an emergency call.
REASK_COOLDOWN_TICKS = 4
REASK_LIMIT = 2

# The holding phrase, for when there is nothing left to ask. Rare, because its
# job is to show the line is open, not to fill every pause.
HOLDING_COOLDOWN_TICKS = 6
HOLDING_LIMIT = 4

# What the call cannot end without. Everything else on the plan is detail an
# operator can chase; without these two there is no incident to hand over, so
# the agent holds the line instead of hanging up on someone.
ESSENTIAL_FIELDS = ("incident_type", "location_text")

# A caller who has said three things has been through the opening exchange.
# Below that, running out of questions means the questions never got going.
MIN_SEGMENTS_BEFORE_CLOSING = 3

# Every question the scenario plan asks for now has an answer: say so,
# reassure the caller, and let the line go.
CLOSING_PHRASE = "wrap_up"


@dataclass
class DialBridge:
    """Per-call state: where we have read up to, and what we have already said."""

    call_id: str
    whisper_size: str = field(default_factory=lambda: settings.dial_whisper_size)
    _offset: int = 0
    # phrase id -> (tick last spoken, times spoken, segments heard by then).
    _spoken: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    _tick: int = 0
    _heard: int = 0
    _busy: bool = False
    _closing: bool = False
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
        self._record_opening_line()
        path = self._pcm_to_wav(pcm)
        started = time.perf_counter()
        try:
            original, english = whisper_engine.transcribe_and_translate(
                path, self.whisper_size
            )
        finally:
            Path(path).unlink(missing_ok=True)

        if original is None or not original.text.strip():
            # Silence, or noise. Nothing is added to the transcript --
            # inventing a segment from a quiet line would be worse than the
            # quiet. But the agent still has to take its turn: a caller who
            # has stopped talking is waiting for something, and this used to
            # return here without ever asking what to say. On a real call
            # that is where the agent went mute, and where a finished call
            # was left open instead of being closed.
            if not any(
                s.speaker is Speaker.CALLER for s in store.get(self.call_id).segments
            ):
                # Nobody has spoken yet. The greeting is still doing its job.
                return None
            phrase_id = self._pick_phrase()
            if phrase_id is not None:
                self._record_agent_line(phrase_id)
            return phrase_id

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

        engine = get_engine(snapshot.decision_engine)
        decision_started = time.perf_counter()
        engine_result = engine.extract(caller_text, caller_english, original.language)
        rule_result = rule_engine.extract(caller_text, caller_english, original.language)
        resolution = resolve_confidence(
            engine_result, rule_result, caller_text, caller_english,
            asr_confidence=original.word_confidence,
            incident_context=reviewed_incident(snapshot),
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

        phrase_id = self._pick_phrase()
        if phrase_id is not None:
            self._record_agent_line(phrase_id)
        return phrase_id

    # ------------------------------------------------------------------
    def _record_agent_line(self, phrase_id: str) -> None:
        """Put what the agent said into the session, as an operator utterance.

        Without this the dashboard shows a caller answering questions that are
        not on screen: "yes, he is breathing" with nothing to say what was
        asked. The text is the approved phrase verbatim -- the same string the
        caller heard -- so the transcript is a record of the call rather than
        a reconstruction of it.

        Attributed to the operator, never the caller: everything the caller is
        taken to have said becomes evidence, and the agent must not end up
        corroborating its own questions.
        """
        language, text = APPROVED[phrase_id]
        now = round((time.perf_counter() - self._started) * 1000)
        try:
            store.commit_append(
                self.call_id,
                Segment(
                    id=store.next_id(self.call_id, "seg"),
                    index=len(store.get(self.call_id).segments),
                    speaker=Speaker.OPERATOR,
                    original_text=text,
                    language=language,
                    start_ms=now,
                    end_ms=now,
                    final=True,
                ),
                [], [], [],
            )
        except Exception as exc:  # never let bookkeeping drop a call
            log.warning("could not record agent line on %s: %s", self.call_id, exc)

    def _record_opening_line(self) -> None:
        """The greeting is spoken by the call loop, not chosen here.

        It still belongs on screen -- it is the first thing the caller heard,
        and the first answer is a reply to it.
        """
        opening = settings.dial_opening_phrase
        if opening in APPROVED and not store.get(self.call_id).segments:
            self._record_agent_line(opening)

    def _say(self, phrase_id: str) -> str:
        _, times, _ = self._spoken.get(phrase_id, (0, 0, 0))
        self._spoken[phrase_id] = (self._tick, times + 1, self._heard)
        return phrase_id

    def _may_say(
        self,
        phrase_id: str,
        *,
        cooldown: int,
        limit: int,
        only_if_unanswered: bool = False,
    ) -> bool:
        if phrase_id not in APPROVED:
            return False
        asked_at, times, heard_at = self._spoken.get(phrase_id, (-999, 0, -1))
        if times >= limit or (self._tick - asked_at) < cooldown:
            return False
        if only_if_unanswered and times and self._heard != heard_at:
            # They replied to this one. Whatever we failed to pull out of that
            # reply is the operator's to chase, not ours to ask again.
            return False
        return True

    def _exhausted(
        self,
        phrase_id: str,
        *,
        limit: int = REASK_LIMIT,
        retire_on_reply: bool = True,
    ) -> bool:
        """True when this phrase will never be said again.

        Either it has hit its limit, or -- for a question -- it was asked and
        the caller replied, and a question that got a reply is not asked
        twice whatever came of the reply. The holding phrase is not a
        question, so a reply does not retire it; only its limit does.
        """
        if phrase_id not in APPROVED:
            return True
        _, times, heard_at = self._spoken.get(phrase_id, (0, 0, -1))
        if times == 0:
            return False
        if times >= limit:
            return True
        return retire_on_reply and self._heard != heard_at

    def _ready_to_close(self, snapshot, questions: list[dict], language: str) -> bool:
        """Whether the call has run its course.

        The first version closed only when the question plan emptied, which on
        a real call never happened: the plan grows with the incident type and
        the extractor does not fill every field from spoken English. A caller
        reached the end of the questions and got silence -- "Hello? Hello?" --
        because the agent had nothing it was allowed to say and no way to
        finish. Running out of things to ask is the end of the call; it just
        has to be said out loud.
        """
        if self._heard < MIN_SEGMENTS_BEFORE_CLOSING:
            return False
        if not all(self._exhausted(q["phrase_id"]) for q in questions):
            return False

        values = observed(snapshot)
        if all(known(values.get(field_name)) for field_name in ESSENTIAL_FIELDS):
            return True

        # We never established what happened or where. Keep the line open
        # while there is still a holding phrase left to say; once even that
        # is gone, closing beats a line that has gone quiet, which is what a
        # caller hears as the call having dropped.
        return self._exhausted(
            f"stay_on_line_{language}",
            limit=HOLDING_LIMIT,
            retire_on_reply=False,
        )

    def should_end_call(self) -> bool:
        """True once the closing phrase has been handed to the call loop.

        The loop hangs up only after the audio has drained, so the caller
        hears the whole reassurance before the line closes.
        """
        return self._closing

    def _pick_phrase(self) -> str | None:
        """The next thing to say, or None to stay quiet.

        The first version of this walked the question list once and returned
        None forever after the last one -- so a caller who got as far as "Are
        you safe right now?" was answered with permanent silence for the rest
        of the call. Two things were wrong with it, and both are fixed here.

        A question was asked once ever, even if the caller never answered it.
        A question nobody responded to is worth asking again after a pause,
        which is what a person would do; ``REASK_COOLDOWN_TICKS`` and
        ``REASK_LIMIT`` keep that from becoming nagging, and
        ``only_if_unanswered`` keeps it from ever being asked at someone who
        did reply.

        And running out of questions is not a reason to go mute. If the plan
        is complete the call is closed properly; if it is merely paused, a
        holding phrase keeps the line from sounding dropped.

        The one case that still produces silence is the one that should: an
        open weapon or trapped-caller flag.
        """
        snapshot = store.get(self.call_id)
        self._tick += 1
        # Caller utterances only. The agent's own lines are in the session
        # too now, and counting them would make every question look answered
        # the moment it was asked.
        self._heard = sum(1 for s in snapshot.segments if s.speaker is Speaker.CALLER)

        if any(flag.kind.value in SILENCING_FLAGS for flag in snapshot.open_flags):
            # Hand over rather than talk. See the module docstring.
            return None

        language = (snapshot.detected_language or "en")[:2]
        if language not in SUPPORTED_LANGUAGES:
            return None

        # A field counts as handled once it is accepted, or once a suggestion
        # for it is waiting on a human -- asking again would talk past them.
        questions = follow_up_questions(snapshot)

        # First pass: anything never asked. Second: anything still unknown
        # whose cooldown has elapsed.
        for cooldown, limit in ((0, 1), (REASK_COOLDOWN_TICKS, REASK_LIMIT)):
            for question in questions:
                phrase_id = question["phrase_id"]
                if self._may_say(
                    phrase_id,
                    cooldown=cooldown,
                    limit=limit,
                    only_if_unanswered=True,
                ):
                    return self._say(phrase_id)

        # Nothing may be asked. If that is because the questions are used up
        # rather than merely resting, finish the call properly.
        if self._ready_to_close(snapshot, questions, language):
            closing = f"{CLOSING_PHRASE}_{language}"
            if self._may_say(closing, cooldown=0, limit=1):
                self._closing = True
                log.info(
                    "call %s: question plan complete; closing the line",
                    self.call_id,
                )
                return self._say(closing)
            return None

        # Questions remain but none may be asked yet. Say something anyway,
        # occasionally: the caller should be able to tell the line is open.
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
