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
)
from app.live.store import SessionStore


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
        agent, _ = bridge
        spoken = [agent._pick_phrase() for _ in range(24)]
        assert spoken.count("repeat_address_en") > 1

    def test_but_it_stops_rather_than_nagging(self, bridge):
        agent, _ = bridge
        spoken = [agent._pick_phrase() for _ in range(60)]
        assert spoken.count("repeat_address_en") <= REASK_LIMIT
        assert spoken.count("stay_on_line_en") <= HOLDING_LIMIT


class TestItAsksForWhatIsMissing:
    def test_it_moves_on_once_a_field_is_answered(self, bridge):
        agent, snapshot = bridge
        assert agent._pick_phrase() == "repeat_address_en"
        answer(snapshot, "location_text", "Oak and Third")
        # Never asks for the address again now that it has one.
        assert "repeat_address_en" not in [agent._pick_phrase() for _ in range(12)]

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
