"""What the agent says on a live call, and when it says nothing.

The bug these were written for: the agent asked its five questions, reached
"Are you safe right now?", and then never spoke again for the rest of the
call. To a caller that is indistinguishable from the line dropping.

No model runs here -- the phrase policy is a pure function of session state,
which is the point of keeping it separate from the transcription path.
"""

from __future__ import annotations

import pytest

from app.live.dial_bridge import (
    HOLDING_LIMIT,
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
)
from app.live.store import SessionStore
from app.tts import APPROVED


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
    """Record that the caller spoke. A reply is what stops a re-ask."""
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


class TestItDoesNotGoSilentForever:
    def test_the_bug_itself(self, bridge):
        """Reaching the last question must not end the conversation.

        Before the fix this returned the five questions and then None on
        every subsequent tick, forever.
        """
        agent, _ = bridge
        spoken = [agent._pick_phrase() for _ in range(24)]
        # The last question is ask_safe; everything after it used to be None.
        after_last_question = spoken[spoken.index("ask_safe_en") + 1 :]
        assert any(x is not None for x in after_last_question)

    def test_it_keeps_speaking_across_a_long_call(self, bridge):
        agent, _ = bridge
        spoken = [agent._pick_phrase() for _ in range(24)]
        assert sum(1 for x in spoken if x) >= 10

    def test_no_silent_gap_long_enough_to_sound_like_a_dropped_call(self, bridge):
        agent, _ = bridge
        longest = run = 0
        for _ in range(24):
            run = run + 1 if agent._pick_phrase() is None else 0
            longest = max(longest, run)
        # Ticks are a few seconds each; a handful is a pause, a dozen is a
        # caller wondering whether anyone is there.
        assert longest <= 6

    def test_an_unanswered_question_comes_back(self, bridge):
        """Nobody said anything. Asking again is what a person would do."""
        agent, _ = bridge
        spoken = [agent._pick_phrase() for _ in range(24)]
        assert spoken.count("repeat_address_en") > 1

    def test_but_it_stops_rather_than_nagging(self, bridge):
        agent, _ = bridge
        spoken = [agent._pick_phrase() for _ in range(60)]
        assert spoken.count("repeat_address_en") <= REASK_LIMIT
        assert spoken.count("stay_on_line_en") <= HOLDING_LIMIT


class TestItDoesNotAskTwiceForSomethingAnswered:
    """The caller's complaint: being asked a question they just answered."""

    def test_a_question_is_not_repeated_after_the_caller_replies(self, bridge):
        agent, snapshot = bridge
        first = agent._pick_phrase()
        assert first == "repeat_address_en"
        # They answered. The extractor got nothing useful out of it -- which
        # is exactly the case that used to produce a repeat.
        heard(snapshot, "it is the big building, you know the one")
        later = [agent._pick_phrase() for _ in range(30)]
        assert first not in later

    def test_an_answered_field_is_never_asked_about(self, bridge):
        agent, snapshot = bridge
        answer(snapshot, "location_text", "Oak and Third")
        heard(snapshot)
        assert "repeat_address_en" not in [agent._pick_phrase() for _ in range(20)]


class TestItClosesTheCallWhenItIsDone:
    def test_it_reassures_the_caller_before_hanging_up(self, bridge):
        agent, snapshot = bridge
        answer_everything(snapshot)
        assert agent._pick_phrase() == "wrap_up_en"
        assert agent.should_end_call() is True

    def test_it_does_not_hang_up_before_that(self, bridge):
        agent, snapshot = bridge
        agent._pick_phrase()
        assert agent.should_end_call() is False
        answer(snapshot, "location_text", "Oak and Third")
        heard(snapshot)
        agent._pick_phrase()
        assert agent.should_end_call() is False

    def test_the_closing_phrase_is_said_once(self, bridge):
        agent, snapshot = bridge
        answer_everything(snapshot)
        spoken = [agent._pick_phrase() for _ in range(10)]
        assert spoken.count("wrap_up_en") == 1
        # And nothing else: there is nothing left to say.
        assert set(spoken) == {"wrap_up_en", None}

    def test_it_closes_in_the_caller_s_language(self, bridge):
        agent, snapshot = bridge
        answer_everything(snapshot)
        snapshot.detected_language = "es"
        assert agent._pick_phrase() == "wrap_up_es"

    def test_a_safety_flag_still_wins(self, bridge):
        """A complete record is not a reason to hang up on a weapon call."""
        agent, snapshot = bridge
        answer_everything(snapshot)
        snapshot.review_flags.append(
            ReviewFlag(
                id="f1", kind=ReviewFlagKind.WEAPON_MENTIONED, text="review this"
            )
        )
        assert agent._pick_phrase() is None
        assert agent.should_end_call() is False

    def test_a_scenario_question_keeps_the_call_open(self, bridge):
        """Codex's scenario questions appear once the type is known.

        Answering the five generic fields is no longer the end of the call if
        the incident type pulled in follow-ups, so the plan -- not a fixed
        list -- decides when there is nothing left to ask.
        """
        agent, snapshot = bridge
        answer_everything(snapshot)
        answer(snapshot, "incident_type", "structure_fire")
        phrases = [agent._pick_phrase() for _ in range(6)]
        assert "wrap_up_en" not in phrases
        assert any(p and p.startswith("ask_") for p in phrases)
        assert agent.should_end_call() is False


class TestTheCallThatWentQuiet:
    """From a real 100-second call to the live number.

    The caller gave an address, said the fire was in 6B, said everyone was
    out. The agent asked its questions, ran out of ones it was allowed to
    repeat, played its holding lines, and then said nothing at all. The last
    two things on the transcript are the caller saying "Hello?" twice.

    The questions do not have to all be answered for a call to be over. They
    only have to be used up.
    """

    def test_it_does_not_trail_off_when_the_questions_run_out(self, bridge):
        agent, snapshot = bridge
        # What the extractor managed to get from that call: the essentials,
        # and nothing else.
        answer(snapshot, "incident_type", "structure_fire")
        answer(snapshot, "location_text", "1400 Birch Court, apartment 6A")

        spoken = []
        for _ in range(40):
            # The caller keeps talking, as that caller did.
            heard(snapshot, "and the stairs are full of smoke")
            spoken.append(agent._pick_phrase())

        assert "wrap_up_en" in spoken, "the call never finished"
        # And once it has finished it is not still trailing holding phrases.
        after = spoken[spoken.index("wrap_up_en") + 1 :]
        assert set(after) <= {None}
        assert agent.should_end_call() is True

    def test_it_holds_the_line_rather_than_closing_on_an_unknown_incident(self, bridge):
        """Nothing established: hanging up is the wrong end of the call."""
        agent, snapshot = bridge
        spoken = []
        for _ in range(12):
            heard(snapshot, "...")
            spoken.append(agent._pick_phrase())
        assert "wrap_up_en" not in spoken
        assert "stay_on_line_en" in spoken

    def test_but_a_line_with_nothing_left_to_say_is_closed_not_abandoned(self, bridge):
        """Even with nothing established, silence is not the answer.

        Once the questions and the holding phrases are both used up there is
        nothing further an approved-phrase agent can do, and a caller left on
        an open line hears a call that dropped.
        """
        agent, snapshot = bridge
        spoken = []
        for _ in range(60):
            heard(snapshot, "...")
            spoken.append(agent._pick_phrase())
        assert "wrap_up_en" in spoken


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


class TestItAsksForWhatIsMissing:
    def test_it_moves_on_once_a_field_is_answered(self, bridge):
        agent, snapshot = bridge
        assert agent._pick_phrase() == "repeat_address_en"
        answer(snapshot, "location_text", "Oak and Third")
        # Never asks for the address again now that it has one.
        assert "repeat_address_en" not in [agent._pick_phrase() for _ in range(12)]

    def test_it_asks_the_scenario_s_own_questions(self, bridge):
        """The plan the operator sees is the plan the phone follows."""
        agent, snapshot = bridge
        answer(snapshot, "incident_type", "medical")
        heard(snapshot)
        spoken = {p for p in (agent._pick_phrase() for _ in range(20)) if p}
        assert any(p not in ("stay_on_line_en", "wrap_up_en") for p in spoken)
        assert "what_happening_en" not in spoken  # the type is established

    def test_a_pending_suggestion_counts_as_heard(self, bridge):
        agent, snapshot = bridge
        from app.live.models import Suggestion, UpdateKind

        snapshot.suggestions.append(
            Suggestion(id="s1", field="location_text", kind=UpdateKind.ADDITION,
                       proposed_value="Oak and Third", base_revision=0)
        )
        # It was said; it is waiting for a human. Asking again talks past them.
        assert "repeat_address_en" not in [agent._pick_phrase() for _ in range(8)]

    def test_it_asks_in_the_caller_s_language(self, bridge):
        agent, snapshot = bridge
        snapshot.detected_language = "es"
        assert agent._pick_phrase().endswith("_es")

    def test_it_says_nothing_in_a_language_it_cannot_speak(self, bridge):
        agent, snapshot = bridge
        snapshot.detected_language = "pt"
        assert all(agent._pick_phrase() is None for _ in range(5))


class TestSilenceWhereSilenceIsCorrect:
    @pytest.mark.parametrize(
        "kind",
        [ReviewFlagKind.WEAPON_MENTIONED, ReviewFlagKind.CALLER_MAY_BE_TRAPPED],
    )
    def test_a_safety_flag_silences_it_completely(self, bridge, kind):
        """The one case that must still produce silence.

        A synthetic voice asking a scripted question over someone who has
        just said there is a weapon is worth less than nothing.
        """
        agent, snapshot = bridge
        snapshot.review_flags.append(
            ReviewFlag(id="f1", kind=kind, text="review this statement")
        )
        assert all(agent._pick_phrase() is None for _ in range(10))

    def test_an_acknowledged_flag_lets_it_speak_again(self, bridge):
        agent, snapshot = bridge
        flag = ReviewFlag(id="f1", kind=ReviewFlagKind.WEAPON_MENTIONED, text="review")
        snapshot.review_flags.append(flag)
        assert agent._pick_phrase() is None
        # A human has seen it and taken the call on; the agent is no longer
        # talking over an unreviewed safety statement.
        flag.status = "acknowledged"
        assert agent._pick_phrase() is not None
