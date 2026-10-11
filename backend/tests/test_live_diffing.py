"""The rules that decide whether a caller's words change the record.

These are the tests that would catch the failure the issue is really about:
a later statement silently replacing an earlier one that a human had already
approved.
"""

from __future__ import annotations

import pytest

from app.live.diffing import (
    apply_ordinal_correction,
    build_suggestions,
    cp_to_utf16,
    has_correction_cue,
    utf16_len,
)
from app.live.models import (
    FieldValue,
    Presence,
    ReviewFlagKind,
    Segment,
    Speaker,
    UpdateKind,
)
from app.schemas import Prediction


@pytest.fixture
def next_id():
    counter = {"n": 0}

    def make(prefix: str) -> str:
        counter["n"] += 1
        return f"{prefix}-{counter['n']}"

    return make


def segment(index: int, text: str, translation: str | None = None) -> Segment:
    return Segment(
        id=f"seg-{index}",
        index=index,
        speaker=Speaker.CALLER,
        original_text=text,
        translation=translation,
    )


def accepted_location(value: str = "Oak and Third") -> dict:
    return {
        "location_text": FieldValue(
            field="location_text", value=value, presence=Presence.STATED,
            revision=1, source_segment_ids=["seg-1"],
        )
    }


class TestOffsets:
    def test_accented_latin_agrees_in_both_unit_systems(self):
        # Worth asserting because the usual justification for this convention
        # cites accents, and accents are not actually where it bites.
        for text in ("Juárez", "perdón", "Morelos"):
            assert len(text) == utf16_len(text)

    def test_astral_characters_are_where_the_units_diverge(self):
        text = "🚒 at Oak"
        assert len(text) == 8
        assert utf16_len(text) == 9
        # A JavaScript client slicing at the Python index would cut the emoji
        # in half and land one character early on everything after it.
        assert cp_to_utf16(text, 3) == 4

    def test_conversion_is_identity_for_plain_ascii(self):
        text = "Oak and Third"
        assert all(cp_to_utf16(text, i) == i for i in range(len(text) + 1))


class TestCorrectionCues:
    @pytest.mark.parametrize(
        "text",
        ["Sorry, Fourth, not Third", "Actually it's Pine", "I meant Oak",
         "Perdón, es la Cuarta", "no wait, Main Street", "quise decir Morelos"],
    )
    def test_a_retraction_is_recognised(self, text):
        assert has_correction_cue(text)

    @pytest.mark.parametrize(
        "text",
        ["We are at Oak and Third", "Eastbound side", "There is smoke",
         "Hay humo en la cocina"],
    )
    def test_an_ordinary_statement_is_not_a_retraction(self, text):
        assert not has_correction_cue(text)

    def test_the_ordinal_rebuild(self):
        # "Sorry, Fourth, not Third" names the new ordinal and the old one but
        # never the street, so no extractor can produce a whole location from
        # it. This is what turns it back into one.
        assert apply_ordinal_correction("Oak and Third", "Sorry, Fourth, not Third") == "Oak and Fourth"

    def test_the_rebuild_refuses_when_there_is_nothing_to_swap(self):
        assert apply_ordinal_correction("Oak and Third", "the fire is spreading") is None
        assert apply_ordinal_correction("418 Alameda Street", "sorry, wrong") is None

    def test_the_rebuild_refuses_an_ambiguous_swap(self):
        # Two new ordinals: which one did they mean? Guessing would be worse
        # than handing it to the operator.
        assert apply_ordinal_correction("Oak and Third", "sorry, Fourth or Fifth") is None


class TestTheDecisionTable:
    def test_nothing_accepted_yields_an_addition(self, next_id):
        out = build_suggestions(
            accepted={}, pending=[],
            predictions=[Prediction(label="location_text", value="Oak and Third", score=0.8)],
            segment=segment(1, "We are at Oak and Third"), base_revision=0, next_id=next_id,
        )
        assert [s.kind for s in out.suggestions] == [UpdateKind.ADDITION]
        assert out.suggestions[0].previous_value is None

    def test_a_retracted_difference_is_a_correction(self, next_id):
        out = build_suggestions(
            accepted=accepted_location(), pending=[],
            predictions=[Prediction(label="location_text", value="Fourth", score=0.7)],
            segment=segment(3, "Sorry, Fourth, not Third"), base_revision=1, next_id=next_id,
        )
        suggestion = out.suggestions[0]
        assert suggestion.kind is UpdateKind.CORRECTION
        assert suggestion.proposed_value == "Oak and Fourth"
        assert suggestion.previous_value == "Oak and Third"

    def test_an_unretracted_difference_is_a_conflict_not_an_overwrite(self, next_id):
        # This is the rule that matters most. A later location with no
        # retraction might be a cross street or a landmark; proposing it as a
        # correction would be an overwrite with a friendlier label.
        out = build_suggestions(
            accepted=accepted_location(), pending=[],
            predictions=[Prediction(label="location_text", value="Pine and Main", score=0.7)],
            segment=segment(4, "Pine and Main"), base_revision=1, next_id=next_id,
        )
        suggestion = out.suggestions[0]
        assert suggestion.kind is UpdateKind.CONFLICT
        assert suggestion.proposed_value is None
        assert {o.value for o in suggestion.conflict_options} == {"Oak and Third", "Pine and Main"}
        assert any(f.kind is ReviewFlagKind.CONTRADICTORY_STATEMENTS for f in out.flags)

    def test_agreement_raises_nothing(self, next_id):
        out = build_suggestions(
            accepted=accepted_location(), pending=[],
            predictions=[Prediction(label="location_text", value="Oak and Third", score=0.9)],
            segment=segment(5, "still at Oak and Third"), base_revision=1, next_id=next_id,
        )
        assert out.suggestions == []

    def test_an_absent_value_never_proposes_erasing_an_accepted_one(self, next_id):
        for empty in (None, "", "unknown", []):
            out = build_suggestions(
                accepted=accepted_location(), pending=[],
                predictions=[Prediction(label="location_text", value=empty, score=0.0)],
                segment=segment(6, "..."), base_revision=1, next_id=next_id,
            )
            assert out.suggestions == []

    def test_a_list_field_gains_items_rather_than_being_replaced(self, next_id):
        accepted = {
            "hazards": FieldValue(field="hazards", value=["smoke"],
                                  presence=Presence.STATED, revision=1)
        }
        out = build_suggestions(
            accepted=accepted, pending=[],
            predictions=[Prediction(label="hazards", value=["smoke", "fire"], score=0.8)],
            segment=segment(7, "and now flames"), base_revision=1, next_id=next_id,
        )
        assert out.suggestions[0].proposed_value == ["fire", "smoke"]


class TestOverInferenceGuards:
    def test_a_vague_quantity_becomes_an_unresolved_item_not_a_count(self, next_id):
        out = build_suggestions(
            accepted={}, pending=[], predictions=[],
            segment=segment(1, "There is another person in the second car"),
            base_revision=0, next_id=next_id,
        )
        assert out.suggestions == []
        assert len(out.unresolved) == 1
        assert out.unresolved[0].field == "persons_involved"
        assert any(f.kind is ReviewFlagKind.PERSON_COUNT_UNCERTAIN for f in out.flags)

    def test_the_same_guard_in_spanish(self, next_id):
        out = build_suggestions(
            accepted={}, pending=[], predictions=[],
            segment=segment(1, "Hay otra persona en el segundo carro"),
            base_revision=0, next_id=next_id,
        )
        assert len(out.unresolved) == 1

    def test_an_injury_is_never_proposed_on_a_bare_model_score(self, next_id):
        # Measured on a near-empty call: the model returned suspected at 0.785
        # with nothing in the transcript supporting it. A fabricated injury is
        # the one false positive here with a real cost.
        out = build_suggestions(
            accepted={}, pending=[],
            predictions=[Prediction(label="injuries", value="suspected", score=0.785,
                                    evidence=None)],
            segment=segment(1, "something is wrong"), base_revision=0, next_id=next_id,
        )
        assert out.suggestions == []


class TestSuperseding:
    def test_a_newer_pending_value_retires_the_older_one(self, next_id):
        first = build_suggestions(
            accepted={}, pending=[],
            predictions=[Prediction(label="callback_number", value="555 0147", score=0.9)],
            segment=segment(1, "555 0147"), base_revision=0, next_id=next_id,
        ).suggestions[0]
        out = build_suggestions(
            accepted={}, pending=[first],
            predictions=[Prediction(label="callback_number", value="555 0192", score=0.9)],
            segment=segment(2, "555 0192"), base_revision=0, next_id=next_id,
        )
        assert out.superseded == {first.id: out.suggestions[0].id}

    def test_repeating_the_same_value_strengthens_rather_than_stacks(self, next_id):
        first = build_suggestions(
            accepted={}, pending=[],
            predictions=[Prediction(label="callback_number", value="555 0147", score=0.9)],
            segment=segment(1, "555 0147"), base_revision=0, next_id=next_id,
        ).suggestions[0]
        out = build_suggestions(
            accepted={}, pending=[first],
            predictions=[Prediction(label="callback_number", value="555 0147", score=0.9)],
            segment=segment(2, "I said 555 0147"), base_revision=0, next_id=next_id,
        )
        # Without this, cumulative re-inference re-proposes the same fact on
        # every append and the queue fills with duplicates.
        assert out.corroborated == {first.id: ["seg-2"]}


class TestTheDiffNeverWrites:
    def test_the_accepted_record_is_untouched_by_any_diff(self, next_id):
        accepted = accepted_location()
        before = accepted["location_text"].model_dump()
        for text, value in [
            ("Pine and Main", "Pine and Main"),
            ("Sorry, Fourth, not Third", "Fourth"),
            ("still Oak and Third", "Oak and Third"),
            ("another person here", None),
        ]:
            build_suggestions(
                accepted=accepted, pending=[],
                predictions=[Prediction(label="location_text", value=value, score=0.7)],
                segment=segment(9, text), base_revision=1, next_id=next_id,
            )
        # The structural guarantee: inference proposes, it does not write.
        assert accepted["location_text"].model_dump() == before


def test_unsupported_weapon_denial_is_not_proposed(next_id):
    out = build_suggestions(
        accepted={}, pending=[],
        predictions=[Prediction(label="weapons_present", value="no", score=0.35)],
        segment=segment(1, "There is smoke in my kitchen"), base_revision=0, next_id=next_id,
    )
    assert out.suggestions == []


def test_old_evidence_is_not_attached_to_new_segment():
    from app.live.diffing import _evidence_for_prediction
    from app.schemas import EvidenceSpan
    prediction = Prediction(label="location_text", value="Oak and Third", score=0.8,
                            evidence=EvidenceSpan(text="Oak and Third", start=0, end=13,
                                                  source="original", match="exact"))
    assert _evidence_for_prediction(prediction, segment(4, "My roommate burned his hand")) is None


def test_burn_injury_has_locatable_support():
    from app.confidence import _evidence_for
    text = "My roommate burned his hand. He's awake and talking"
    evidence = _evidence_for("injuries", None, text, text)
    assert evidence is not None
    assert "burned his hand" in evidence.text
