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
* **Generated follow-ups with fixed fallback.** After extraction, the assistant
  drafts a summary and question. Eligible questions are rendered before being
  returned; a provider or speech failure falls back to the approved phrase.
* **Nothing asked once it is known.** The questions come from
  ``live.followup.follow_up_questions``, the same plan the operator view
  shows. Topics and spoken wording are tracked across scenario changes so
  each question is asked once, even when extraction cannot fill the answer.
* **One question, then room to answer.** After speaking, the agent waits for
  the caller, or for a real pause, before speaking again.

When the questions are used up the agent says so and the loop hangs up once
that audio has drained. Nothing is dispatched by this prototype; the record
still waits on a human.

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
from ..tts import APPROVED, synthesize_question
from ..transport.dial_call import GeneratedReply
from . import assistant
from .diffing import build_suggestions
from .followup import observed, reviewed_incident
from ..scenarios import known
from .models import Diagnostics, Segment, Speaker
from .store import SessionNotFound, store

log = logging.getLogger("dispatchiq.dial.bridge")

rule_engine = RuleEngine()

# Safety flags that stop the agent speaking entirely.
SILENCING_FLAGS = {"weapon_mentioned", "caller_may_be_trapped"}

# How many quiet decision ticks (settings.dial_decide_every_s each) to leave
# after speaking before speaking again. The caller gets time to answer; the
# agent does not stack a second question on top of an unanswered first.
QUIET_PATIENCE_TICKS = 3

# The holding phrase is for one situation only: a quiet line, early in the
# call, with nothing to ask. It is not what the agent says when it has run
# out of questions -- that is the end of the call, and it says so.
HOLDING_LIMIT = 2

# What the closing phrase is allowed to claim. With both of these known the
# agent says it has what it needs; without them it says only that a dispatcher
# has what the caller told it.
ESSENTIAL_FIELDS = ("incident_type", "location_text")

# A caller who has said three things has been through the opening exchange.
# Below that, running out of questions means the questions never got going.
MIN_SEGMENTS_BEFORE_CLOSING = 3

CLOSING_PHRASE = "wrap_up"
PARTIAL_CLOSING_PHRASE = "wrap_up_partial"


@dataclass
class DialBridge:
    """Per-call state: where we have read up to, and what we have already said."""

    call_id: str
    whisper_size: str = field(default_factory=lambda: settings.dial_whisper_size)
    _offset: int = 0
    # phrase id -> (tick last spoken, times spoken, segments heard by then).
    _spoken: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    _asked_fields: set[str] = field(default_factory=set)
    _asked_wordings: set[str] = field(default_factory=set)
    _tick: int = 0
    _heard: int = 0
    # The last question asked, and where the call stood when the agent last
    # said anything: how many caller utterances there were, and on which tick.
    _last_question: str | None = None
    _heard_at_last_line: int = -1
    _last_line_tick: int = 0
    _busy: bool = False
    _closing: bool = False
    _started: float = field(default_factory=time.perf_counter)

    # ------------------------------------------------------------------
    def ensure_session(self) -> None:
        if not store.exists(self.call_id):
            store.create(self.call_id)
            store.set_phone_demo_mode(self.call_id, settings.dial_demo_mode)
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
    async def choose_reply(self, session) -> str | GeneratedReply | None:
        """Called at a speech boundary or on an idle tick by ``CallLoop``."""
        # A tick that arrives while the previous one is still transcribing is
        # dropped rather than queued. A backlog would put older answers on
        # screen after newer ones, and the caller has moved on anyway.
        if self._busy:
            return None

        pcm = bytes(session.pcm16k[self._offset :])
        # A completed short answer ("yes", an apartment number) must not be
        # discarded just because it is shorter than the idle chunk minimum.
        minimum = min(settings.dial_min_chunk_s, 0.25) if getattr(session, "utterance_ready", False) else settings.dial_min_chunk_s
        if len(pcm) / 2 / 16000 < minimum:
            return None

        self._busy = True
        self._offset = len(session.pcm16k)
        try:
            return await asyncio.to_thread(self._process_chunk, pcm, allow_reply=not session.ended)
        except Exception as exc:  # a failed tick must not drop the call
            log.warning("dial bridge tick failed for %s: %s", self.call_id, exc)
            return None
        finally:
            self._busy = False

    # ------------------------------------------------------------------
    def _process_chunk(self, pcm: bytes, *, allow_reply: bool = True) -> str | GeneratedReply | None:
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
            if not allow_reply:
                return None
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

        if allow_reply:
            return self._reply_after_decision()
        return None

    def _reply_after_decision(self) -> str | GeneratedReply | None:
        """Summarize extraction, then speak one eligible question with fallback."""
        draft = None
        if assistant.availability()["enabled"]:
            result = assistant.refresh(self.call_id, store)
            if not result["stale"]:
                draft = result["snapshot"].assistant
        snapshot = store.get(self.call_id)
        preferred = draft.target_field if draft and draft.status == "ready" and not snapshot.phone_demo_mode else None
        phrase_id = self._pick_phrase(preferred)
        if phrase_id is None:
            return None
        snapshot = store.get(self.call_id)
        target = next((q for q in follow_up_questions(snapshot) if q["phrase_id"] == phrase_id), None)
        if (not snapshot.phone_demo_mode and draft and draft.status == "ready" and draft.follow_up_question and target
                and target["field"] == draft.target_field
                and draft.input_signature == assistant.signature(snapshot)):
            language = (snapshot.detected_language or "en")[:2]
            audio = synthesize_question(draft.follow_up_question, language)
            # Human review can arrive during synthesis. Do not speak an old draft.
            current = store.get(self.call_id)
            if any(flag.kind.value in SILENCING_FLAGS for flag in current.open_flags):
                return None
            if draft.input_signature != assistant.signature(current):
                return None
            if audio:
                self._record_agent_text(language, draft.follow_up_question, draft.target_field)
                return GeneratedReply(draft.follow_up_question, language, audio)
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
        target = next((q["field"] for q in follow_up_questions(store.get(self.call_id)) if q["phrase_id"] == phrase_id), None)
        self._record_agent_text(language, text, target)

    def _record_agent_text(self, language: str, text: str, question_field: str | None = None) -> None:
        if question_field:
            self._asked_fields.add(question_field)
            self._asked_wordings.add(assistant.question_key(text))
        now = round((time.perf_counter() - self._started) * 1000)
        try:
            store.commit_append(
                self.call_id,
                Segment(
                    id=store.next_id(self.call_id, "seg"),
                    index=len(store.get(self.call_id).segments),
                    speaker=Speaker.OPERATOR,
                    question_field=question_field,
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
        question = next((q for q in follow_up_questions(store.get(self.call_id)) if q["phrase_id"] == phrase_id), None)
        if question:
            self._asked_fields.add(question["field"])
            self._asked_wordings.add(assistant.question_key(APPROVED[phrase_id][1]))
        _, times, _ = self._spoken.get(phrase_id, (0, 0, 0))
        self._spoken[phrase_id] = (self._tick, times + 1, self._heard)
        self._heard_at_last_line = self._heard
        self._last_line_tick = self._tick
        return phrase_id

    def _times(self, phrase_id: str) -> int:
        return self._spoken.get(phrase_id, (0, 0, 0))[1]

    def _next_question(self, questions: list[dict]) -> str | None:
        """The next question worth asking, or None when they are used up.

        Fields and wording are both checked, because two scenario fields can
        share a phrase or ask the same thing under different IDs.
        """
        operators = [s for s in store.get(self.call_id).segments if s.speaker == Speaker.OPERATOR]
        asked_fields = self._asked_fields | {s.question_field for s in operators if s.question_field}
        asked_wordings = self._asked_wordings | {assistant.question_key(s.original_text) for s in operators}
        for question in questions:
            if (question["phrase_id"] in APPROVED and not self._times(question["phrase_id"])
                    and question["field"] not in asked_fields
                    and assistant.question_key(APPROVED[question["phrase_id"]][1]) not in asked_wordings):
                return question["phrase_id"]
        return None

    def _close(self, snapshot, language: str) -> str | None:
        """Finish the call out loud, once.

        Which phrase depends on what is actually known. "I have what I need"
        is only said when the incident and the location are both on the
        record; otherwise the agent says a dispatcher has what the caller
        told it, which is true of any call.
        """
        if self._closing:
            return None
        values = observed(snapshot)
        complete = all(known(values.get(name)) for name in ESSENTIAL_FIELDS)
        phrase_id = f"{CLOSING_PHRASE if complete else PARTIAL_CLOSING_PHRASE}_{language}"
        if phrase_id not in APPROVED:
            return None
        self._closing = True
        log.info(
            "call %s: questions used up (%s); closing the line",
            self.call_id, "essentials known" if complete else "essentials missing",
        )
        return self._say(phrase_id)

    def should_end_call(self) -> bool:
        """True once the closing phrase has been handed to the call loop.

        The loop hangs up only after the audio has drained, so the caller
        hears the whole closing line before the call ends.
        """
        return self._closing

    def _pick_phrase(self, preferred_field: str | None = None) -> str | None:
        """The next thing to say, or None to stay quiet.

        Three earlier versions of this each failed a real caller in a
        different way, and the rules below are what is left after them:

        * It asked five questions and then never spoke again. So running out
          of questions now ends the call, out loud.
        * Question IDs from different scenarios could ask the same thing,
          so topic and wording history now prevents repeats across both the
          generated and fixed reply paths.
        * It asked the next question on every tick, including quiet ones, so
          two questions went out back to back with no room to answer the
          first. So after speaking it waits for a reply, or for a real pause.

        The one case that produces silence throughout is the one that should:
        an open weapon or trapped-caller flag.
        """
        snapshot = store.get(self.call_id)
        self._tick += 1
        # Caller utterances only. The agent's own lines are in the session
        # too, and counting them would make every question look answered the
        # moment it was asked.
        previously_heard = self._heard
        self._heard = sum(1 for s in snapshot.segments if s.speaker is Speaker.CALLER)
        # Whether the caller said anything on this turn at all.
        speaking = self._heard > previously_heard

        if self._closing:
            return None

        if any(flag.kind.value in SILENCING_FLAGS for flag in snapshot.open_flags):
            # Hand over rather than talk. See the module docstring.
            return None

        language = (snapshot.detected_language or "en")[:2]
        if language not in SUPPORTED_LANGUAGES:
            return None

        if snapshot.phone_demo_mode and (
            len(self._asked_fields) >= 6 or time.perf_counter() - self._started >= settings.dial_demo_max_s
        ):
            return self._close(snapshot, language)

        # Only fields that are still unknown. A field counts as known once it
        # is accepted, or once a suggestion for it is waiting on a human.
        questions = follow_up_questions(snapshot)
        if preferred_field:
            questions.sort(key=lambda question: question["field"] != preferred_field)

        still_open = {question["phrase_id"] for question in questions}
        # A reply is the caller speaking -- or the last question's field
        # filling in, which can happen without a new utterance when an
        # operator accepts or types the answer while the caller waits.
        replied = self._heard > self._heard_at_last_line or (
            self._last_question is not None and self._last_question not in still_open
        )
        candidate = None
        if replied:
            candidate = self._next_question(questions)
        else:
            # The line has been quiet since the agent last spoke. Give the
            # caller room before saying anything else.
            patience = 1 if snapshot.phone_demo_mode else QUIET_PATIENCE_TICKS
            if self._tick - self._last_line_tick < patience:
                return None
            candidate = self._next_question(questions)

        if candidate is not None:
            self._last_question = candidate
            return self._say(candidate)

        # Nothing left to ask.
        if snapshot.phone_demo_mode or self._heard >= MIN_SEGMENTS_BEFORE_CLOSING:
            return self._close(snapshot, language)

        # Too early to end the call, and nothing to ask: on a quiet line, say
        # the line is open. If the caller is talking, let them talk.
        holding = f"stay_on_line_{language}"
        paused = self._tick - self._last_line_tick >= QUIET_PATIENCE_TICKS
        if not speaking and paused and holding in APPROVED:
            if self._times(holding) < HOLDING_LIMIT:
                return self._say(holding)
            return self._close(snapshot, language)
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
