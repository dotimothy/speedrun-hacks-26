"""What the agent says on a live call, when it waits, and how it ends one.

Each class here is a way the agent failed a real caller on the live number:

* it asked five questions and never spoke again;
* it asked two questions back to back with no room to answer the first;
* it never re-asked for an address the caller had talked straight past, and
  filled the rest of the call with "please stay on the line with me";
* it never hung up.

No model runs here -- the phrase policy is a function of session state, which
is the point of keeping it separate from the transcription path.
"""

from __future__ import annotations

import pytest

from app.live.dial_bridge import (
    HOLDING_LIMIT,
    QUIET_PATIENCE_TICKS,
    REASK_LIMIT,
    DialBridge,
)
from app.live.models import (
    FieldValue,
    Presence,
    ReviewFlag,
    ReviewFlagKind,
    Segment,
    Speaker,
    Suggestion,
    UpdateKind,
)
from app.live.store import SessionStore
from app.tts import APPROVED

QUESTIONS = {
    "repeat_address_en",
    "what_happening_en",
    "ask_callback_en",
    "ask_injuries_en",
    "ask_safe_en",
}
CLOSINGS = {"wrap_up_en", "wrap_up_partial_en"}


@pytest.fixture
def bridge(monkeypatch):
    """A bridge over its own store, so tests cannot leak into each other."""
    store = SessionStore()
    monkeypatch.setattr("app.live.dial_bridge.store", store)
    store.create("call-1")
    return DialBridge(call_id="call-1"), store.get("call-1")


def answer(snapshot, field: str, value: object, revision: int = 1) -> None:
    snapshot.accepted_record[field] = FieldValue(
        field=field, value=value, presence=Presence.STATED, revision=revision
    )


def heard(snapshot, text: str = "something the caller said") -> None:
    """The caller says something. A reply is what lets the agent move on."""
    snapshot.segments.append(
        Segment(
            id=f"seg-{len(snapshot.segments)}",
            index=len(snapshot.segments),
            speaker=Speaker.CALLER,
            original_text=text,
            language="en",
            final=True,
        )
    )


def talk(agent, snapshot, turns: int, text: str = "and the stairs are full of smoke"):
    """A caller who keeps talking without ever answering the question."""
    spoken = []
    for _ in range(turns):
        heard(snapshot, text)
        spoken.append(agent._pick_phrase())
    return spoken


def stay_quiet(agent, ticks: int):
    """The caller says nothing; the agent still gets its turns."""
    return [agent._pick_phrase() for _ in range(ticks)]


def answer_everything(snapshot) -> None:
    """Fill the whole question plan for the generic scenario.

    Three utterances because a call is not closed on fewer: running out of
    questions in the first few seconds means the questioning never started.
    """
    from app.scenarios import question_plan

    for _ in range(3):
        heard(snapshot)
    for name, _ in question_plan(None):
        answer(snapshot, name, "stated by the caller")


class TestItAsksForWhatIsMissing:
    def test_it_starts_with_the_address(self, bridge):
        agent, snapshot = bridge
        heard(snapshot, "help")
        assert agent._pick_phrase() == "repeat_address_en"

    def test_it_never_asks_about_something_it_already_has(self, bridge):
        agent, snapshot = bridge
        answer(snapshot, "location_text", "Oak and Third")
        assert "repeat_address_en" not in talk(agent, snapshot, 20)

    def test_a_pending_suggestion_counts_as_heard(self, bridge):
        agent, snapshot = bridge
        snapshot.suggestions.append(
            Suggestion(id="s1", field="location_text", kind=UpdateKind.ADDITION,
                       proposed_value="Oak and Third", base_revision=0)
        )
        # It was said; it is waiting for a human. Asking again talks past them.
        assert "repeat_address_en" not in talk(agent, snapshot, 12)

    def test_it_asks_in_the_caller_s_language(self, bridge):
        agent, snapshot = bridge
        snapshot.detected_language = "es"
        heard(snapshot, "ayuda")
        assert agent._pick_phrase().endswith("_es")

    def test_it_says_nothing_in_a_language_it_cannot_speak(self, bridge):
        agent, snapshot = bridge
        snapshot.detected_language = "pt"
        assert not any(talk(agent, snapshot, 5))

    def test_it_asks_the_scenario_s_own_questions(self, bridge):
        """The plan the operator sees is the plan the phone follows."""
        agent, snapshot = bridge
        answer(snapshot, "incident_type", "medical")
        spoken = {p for p in talk(agent, snapshot, 20) if p}
        assert spoken - QUESTIONS - CLOSINGS - {"stay_on_line_en"}
        assert "what_happening_en" not in spoken  # the type is established


class TestItAsksAgainForWhatMatters:
    """The call that was filled with "please stay on the line with me".

    The caller was mid-story when asked for the address, kept telling the
    story, and was never asked again -- a reply had been heard, so the
    question was retired, and the agent had nothing left but its holding line.
    """

    def test_an_address_the_caller_talked_past_is_asked_for_again(self, bridge):
        agent, snapshot = bridge
        spoken = talk(agent, snapshot, 12)
        assert spoken.count("repeat_address_en") == REASK_LIMIT

    def test_so_are_the_incident_and_the_callback(self, bridge):
        agent, snapshot = bridge
        spoken = talk(agent, snapshot, 12)
        assert spoken.count("what_happening_en") == REASK_LIMIT
        assert spoken.count("ask_callback_en") == REASK_LIMIT

    def test_but_a_detail_is_only_asked_for_once(self, bridge):
        agent, snapshot = bridge
        spoken = talk(agent, snapshot, 20)
        assert spoken.count("ask_injuries_en") == 1
        assert spoken.count("ask_safe_en") == 1

    def test_everything_is_asked_once_before_anything_is_asked_twice(self, bridge):
        agent, snapshot = bridge
        spoken = talk(agent, snapshot, 5)
        assert set(spoken) == QUESTIONS

    def test_a_question_is_not_repeated_once_it_is_answered(self, bridge):
        agent, snapshot = bridge
        assert talk(agent, snapshot, 1) == ["repeat_address_en"]
        answer(snapshot, "location_text", "1400 Birch Court")
        assert "repeat_address_en" not in talk(agent, snapshot, 20)

    def test_it_does_not_fill_a_talking_caller_s_call_with_holding_lines(self, bridge):
        agent, snapshot = bridge
        assert "stay_on_line_en" not in talk(agent, snapshot, 30)


class TestItLeavesRoomToAnswer:
    """Two questions went out back to back on a quiet tick."""

    def test_it_does_not_stack_a_second_question_on_an_unanswered_first(self, bridge):
        agent, snapshot = bridge
        assert talk(agent, snapshot, 1) == ["repeat_address_en"]
        assert stay_quiet(agent, QUIET_PATIENCE_TICKS - 1) == [None] * (
            QUIET_PATIENCE_TICKS - 1
        )

    def test_after_a_real_pause_it_puts_the_same_question_again(self, bridge):
        agent, snapshot = bridge
        talk(agent, snapshot, 1)
        said = [p for p in stay_quiet(agent, QUIET_PATIENCE_TICKS) if p]
        assert said == ["repeat_address_en"]

    def test_and_then_moves_on_rather_than_asking_a_third_time(self, bridge):
        agent, snapshot = bridge
        talk(agent, snapshot, 1)
        said = [p for p in stay_quiet(agent, QUIET_PATIENCE_TICKS * 3) if p]
        assert said.count("repeat_address_en") == REASK_LIMIT - 1
        assert "what_happening_en" in said


class TestItEndsTheCall:
    def test_with_everything_known_it_says_it_has_what_it_needs(self, bridge):
        agent, snapshot = bridge
        answer_everything(snapshot)
        assert agent._pick_phrase() == "wrap_up_en"
        assert agent.should_end_call() is True

    def test_it_does_not_hang_up_while_there_is_something_to_ask(self, bridge):
        agent, snapshot = bridge
        talk(agent, snapshot, 3)
        assert agent.should_end_call() is False

    def test_a_caller_who_never_gives_the_essentials_still_gets_an_ending(self, bridge):
        """From the live number: 47 seconds, no address, no hangup."""
        agent, snapshot = bridge
        spoken = talk(agent, snapshot, 20)
        assert "wrap_up_partial_en" in spoken
        assert agent.should_end_call() is True

    def test_and_that_ending_does_not_claim_to_have_what_it_needs(self, bridge):
        agent, snapshot = bridge
        spoken = talk(agent, snapshot, 20)
        assert "wrap_up_en" not in spoken
        assert "have what i need" not in APPROVED["wrap_up_partial_en"][1].lower()

    def test_with_the_essentials_it_closes_on_the_full_phrase(self, bridge):
        agent, snapshot = bridge
        answer(snapshot, "incident_type", "structure_fire")
        answer(snapshot, "location_text", "1400 Birch Court, apartment 6A")
        spoken = talk(agent, snapshot, 40)
        assert "wrap_up_en" in spoken

    def test_nothing_is_said_after_the_closing_phrase(self, bridge):
        agent, snapshot = bridge
        answer_everything(snapshot)
        spoken = [agent._pick_phrase()] + talk(agent, snapshot, 10) + stay_quiet(agent, 10)
        assert spoken[0] == "wrap_up_en"
        assert set(spoken[1:]) == {None}

    def test_it_closes_in_the_caller_s_language(self, bridge):
        agent, snapshot = bridge
        answer_everything(snapshot)
        snapshot.detected_language = "es"
        assert agent._pick_phrase() == "wrap_up_es"

    def test_a_scenario_question_keeps_the_call_open(self, bridge):
        """Scenario questions appear once the incident type is known, so the
        plan -- not a fixed list of five -- decides when the asking is done."""
        agent, snapshot = bridge
        answer_everything(snapshot)
        answer(snapshot, "incident_type", "structure_fire")
        heard(snapshot)
        first = agent._pick_phrase()
        assert first not in CLOSINGS and first is not None
        assert agent.should_end_call() is False

    def test_a_call_that_never_got_going_is_held_before_it_is_ended(self, bridge):
        """One utterance, then nothing. Too early to hang up on anyone."""
        agent, snapshot = bridge
        answer_everything(snapshot)
        del snapshot.segments[1:]
        said = [p for p in stay_quiet(agent, 4) if p]
        assert said and said[0] == "stay_on_line_en"

    def test_but_not_held_forever(self, bridge):
        agent, snapshot = bridge
        answer_everything(snapshot)
        del snapshot.segments[1:]
        said = [p for p in stay_quiet(agent, 40) if p]
        assert said.count("stay_on_line_en") == HOLDING_LIMIT
        assert said[-1] in CLOSINGS


class TestAQuietLine:
    """The caller has stopped talking. That is a turn, not an absence of one."""

    @pytest.fixture
    def quiet(self, monkeypatch):
        # Whisper hears nothing: the chunk is silence.
        monkeypatch.setattr(
            "app.live.dial_bridge.whisper_engine.transcribe_and_translate",
            lambda path, size: (None, None),
        )

    def test_the_agent_still_takes_its_turn(self, bridge, quiet):
        agent, snapshot = bridge
        heard(snapshot, "there is a fire")
        said = [agent._process_chunk(bytes(64000)) for _ in range(8)]
        assert any(said), "silence from the caller produced silence from the agent"

    def test_a_finished_call_is_closed_even_if_nobody_speaks_again(self, bridge, quiet):
        """This is the hangup that did not happen on the live number."""
        agent, snapshot = bridge
        answer_everything(snapshot)
        said = [agent._process_chunk(bytes(64000)) for _ in range(4)]
        assert "wrap_up_en" in said
        assert agent.should_end_call() is True

    def test_silence_adds_nothing_to_the_caller_s_transcript(self, bridge, quiet):
        agent, snapshot = bridge
        heard(snapshot, "there is a fire")
        for _ in range(6):
            agent._process_chunk(bytes(64000))
        assert sum(1 for s in snapshot.segments if s.speaker is Speaker.CALLER) == 1

    def test_it_does_not_talk_over_its_own_greeting(self, bridge, quiet):
        """Before the caller's first word there is nothing to respond to."""
        agent, _ = bridge
        assert [agent._process_chunk(bytes(64000)) for _ in range(5)] == [None] * 5


class TestWhatTheOperatorSees:
    def test_the_agent_s_questions_are_written_into_the_session(self, bridge):
        """An answer on screen is unreadable without the question."""
        agent, snapshot = bridge
        agent._record_agent_line("ask_injuries_en")
        spoken = [s for s in snapshot.segments if s.speaker is Speaker.OPERATOR]
        assert len(spoken) == 1
        assert spoken[0].original_text == APPROVED["ask_injuries_en"][1]

    def test_what_the_agent_said_is_never_attributed_to_the_caller(self, bridge):
        """Otherwise the agent corroborates its own questions as evidence."""
        agent, snapshot = bridge
        agent._record_agent_line("ask_safe_en")
        assert all(s.speaker is not Speaker.CALLER for s in snapshot.segments)

    def test_the_agent_s_own_lines_do_not_count_as_the_caller_replying(self, bridge):
        agent, snapshot = bridge
        talk(agent, snapshot, 1)
        agent._record_agent_line("repeat_address_en")
        assert agent._pick_phrase() is None  # still waiting for the caller


class TestSilenceWhereSilenceIsCorrect:
    @pytest.mark.parametrize(
        "kind",
        [ReviewFlagKind.WEAPON_MENTIONED, ReviewFlagKind.CALLER_MAY_BE_TRAPPED],
    )
    def test_a_safety_flag_silences_it_completely(self, bridge, kind):
        """A synthetic voice asking a scripted question over someone who has
        just said there is a weapon is worth less than nothing."""
        agent, snapshot = bridge
        snapshot.review_flags.append(
            ReviewFlag(id="f1", kind=kind, text="review this statement")
        )
        assert not any(talk(agent, snapshot, 10))

    def test_a_safety_flag_also_stops_it_hanging_up(self, bridge):
        """A complete record is not a reason to end a weapon call."""
        agent, snapshot = bridge
        answer_everything(snapshot)
        snapshot.review_flags.append(
            ReviewFlag(id="f1", kind=ReviewFlagKind.WEAPON_MENTIONED, text="review")
        )
        assert agent._pick_phrase() is None
        assert agent.should_end_call() is False

    def test_an_acknowledged_flag_lets_it_speak_again(self, bridge):
        agent, snapshot = bridge
        flag = ReviewFlag(id="f1", kind=ReviewFlagKind.WEAPON_MENTIONED, text="review")
        snapshot.review_flags.append(flag)
        heard(snapshot)
        assert agent._pick_phrase() is None
        flag.status = "acknowledged"
        heard(snapshot)
        assert agent._pick_phrase() is not None
