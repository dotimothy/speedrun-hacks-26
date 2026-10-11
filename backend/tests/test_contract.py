"""The invariants the product's credibility rests on.

None of these need a GPU, a model or a network. That is the point of keeping
the policy in pure functions: the rules that matter can be checked in under a
second, so there is no excuse for not checking them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evidence import locate, locate_term, resolve
from app.schemas import (
    CRITICAL_FIELDS,
    IncidentForm,
    Injuries,
    Prediction,
    SuggestedAction,
    Tristate,
    VerificationState,
)
from app.workflow import apply, is_unknown

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "scenarios"


def preds(**values: object) -> list[Prediction]:
    return [Prediction(label=k, value=v, score=0.8) for k, v in values.items()]


def complete() -> list[Prediction]:
    return preds(
        incident_type="medical",
        location_text="418 Alameda Street",
        callback_number="555-0147",
    )


# ---------------------------------------------------------------------------
# Invariant 1: unknown is not false
# ---------------------------------------------------------------------------
class TestUnknownIsNotFalse:
    def test_negative_answers_are_answers(self):
        # A caller saying "nobody is hurt" is information. Treating it as
        # missing would make the dispatcher ask a question already answered.
        assert is_unknown("none") is False
        assert is_unknown("no") is False
        assert is_unknown(0) is False
        assert is_unknown(False) is False

    def test_absence_is_unknown(self):
        assert is_unknown("unknown") is True
        assert is_unknown(None) is True
        assert is_unknown("") is True
        assert is_unknown([]) is True

    def test_enums_keep_both_members(self):
        assert Injuries.NONE != Injuries.UNKNOWN
        assert Tristate.NO != Tristate.UNKNOWN
        assert len({Injuries.NONE.value, Injuries.UNKNOWN.value}) == 2

    def test_no_coercion_through_the_workflow(self):
        result = apply(preds(injuries="none", **{}), IncidentForm())
        assert result.predictions[0].value == "none"

    def test_injuries_none_does_not_become_missing(self):
        # "none" must not land in missing_fields via an unknown check.
        result = apply(
            preds(incident_type="medical", location_text="1 A St",
                  callback_number="555-0100", injuries="none"),
            IncidentForm(),
        )
        assert "injuries" not in result.missing_fields


# ---------------------------------------------------------------------------
# Invariant 2: a human's value is never overwritten
# ---------------------------------------------------------------------------
class TestConfirmedFieldsAreProtected:
    def test_model_cannot_overwrite_a_confirmed_value(self):
        result = apply(
            preds(incident_type="gas_leak"),
            IncidentForm(),
            confirmed={"incident_type": "structure_fire"},
        )
        pred = result.predictions[0]
        assert pred.value == "structure_fire"
        assert pred.verification_state is VerificationState.CORRECTED
        # The model's view is preserved rather than discarded, so the UI can
        # show that it now disagrees.
        assert pred.alternative == "gas_leak"
        assert pred.conflict is True

    def test_agreement_is_marked_confirmed_not_corrected(self):
        result = apply(
            preds(incident_type="medical"),
            IncidentForm(),
            confirmed={"incident_type": "medical"},
        )
        assert result.predictions[0].verification_state is VerificationState.CONFIRMED
        assert result.predictions[0].alternative is None

    def test_confirmation_survives_repeated_passes(self):
        confirmed = {"incident_type": "structure_fire"}
        for model_guess in ("gas_leak", "medical", "other", "unknown"):
            result = apply(preds(incident_type=model_guess), IncidentForm(), confirmed=confirmed)
            assert result.predictions[0].value == "structure_fire"

    def test_confirmed_field_reaches_the_form(self):
        result = apply(
            preds(incident_type="gas_leak"),
            IncidentForm(),
            confirmed={"incident_type": "structure_fire"},
        )
        assert result.form.incident_type.value == "structure_fire"


# ---------------------------------------------------------------------------
# Invariant 3: nothing is actionable without a human
# ---------------------------------------------------------------------------
class TestNoAutomatedDispatch:
    def test_the_enum_contains_no_dispatch_action(self):
        # The guardrail is the absence of a value, so no model confidence can
        # produce one.
        banned = ("dispatch", "send", "unit", "engine", "ambulance", "deploy")
        for action in SuggestedAction:
            assert not any(word in action.value for word in banned), action

    def test_requires_review_is_always_true(self):
        cases = [
            preds(incident_type="unknown"),
            complete(),
            preds(incident_type="structure_fire", weapons_present="yes"),
        ]
        for case in cases:
            assert apply(case, IncidentForm()).requires_review is True

    def test_requires_review_true_even_when_fully_confirmed(self):
        confirmed = {f: "x" for f in CRITICAL_FIELDS}
        assert apply(complete(), IncidentForm(), confirmed=confirmed).requires_review is True

    def test_ready_for_submission_is_unreachable_from_model_output(self):
        # Every critical field present and confident, but unconfirmed.
        result = apply(complete(), IncidentForm())
        assert result.suggested_action is not SuggestedAction.READY_FOR_HUMAN_SUBMISSION

    def test_ready_for_submission_requires_human_confirmation(self):
        confirmed = {
            "incident_type": "medical",
            "location_text": "418 Alameda Street",
            "callback_number": "555-0147",
        }
        result = apply(complete(), IncidentForm(), confirmed=confirmed)
        assert result.suggested_action is SuggestedAction.READY_FOR_HUMAN_SUBMISSION


# ---------------------------------------------------------------------------
# The decision table
# ---------------------------------------------------------------------------
class TestDecisionTable:
    def test_silence_waits_instead_of_inventing(self):
        result = apply(preds(incident_type="unknown"), IncidentForm(), has_speech=False)
        assert result.suggested_action is SuggestedAction.AWAIT_MORE_AUDIO

    def test_doubtful_language_asks_for_support(self):
        result = apply(complete(), IncidentForm(), language_probability=0.36)
        assert result.suggested_action is SuggestedAction.REQUEST_LANGUAGE_SUPPORT

    def test_weapon_escalates(self):
        case = complete() + preds(weapons_present="yes")
        assert apply(case, IncidentForm()).suggested_action is SuggestedAction.ESCALATE_TO_HUMAN_NOW

    def test_trapped_caller_escalates(self):
        case = complete() + preds(caller_safe="no")
        assert apply(case, IncidentForm()).suggested_action is SuggestedAction.ESCALATE_TO_HUMAN_NOW

    def test_unknown_safety_does_not_escalate(self):
        # Not knowing whether the caller is safe is not the same as knowing
        # they are in danger. If it escalated, every call would escalate.
        case = complete() + preds(caller_safe="unknown", weapons_present="unknown")
        assert apply(case, IncidentForm()).suggested_action is SuggestedAction.REQUEST_HUMAN_CONFIRMATION

    def test_missing_critical_fields_are_named_with_prompts(self):
        result = apply(preds(incident_type="structure_fire"), IncidentForm())
        assert result.suggested_action is SuggestedAction.REQUEST_MISSING_CRITICAL_FIELD
        assert set(result.missing_fields) == {"location_text", "callback_number"}
        assert len(result.prompts) == 2

    def test_escalation_outranks_missing_fields(self):
        result = apply(preds(incident_type="unknown", weapons_present="yes"), IncidentForm())
        assert result.suggested_action is SuggestedAction.ESCALATE_TO_HUMAN_NOW

    def test_silence_outranks_everything(self):
        result = apply(complete() + preds(weapons_present="yes"), IncidentForm(), has_speech=False)
        assert result.suggested_action is SuggestedAction.AWAIT_MORE_AUDIO


# ---------------------------------------------------------------------------
# Evidence must be real
# ---------------------------------------------------------------------------
class TestEvidence:
    TRANSCRIPT = "There is smoke coming from my kitchen and my son is coughing badly."

    def test_verbatim_quote_resolves_to_offsets_that_slice_back(self):
        span = locate("smoke coming from my kitchen", self.TRANSCRIPT)
        assert span is not None
        assert self.TRANSCRIPT[span.start : span.end] == span.text
        assert span.match == "exact"

    def test_punctuation_and_case_drift_is_tolerated(self):
        span = locate("Smoke  coming from my KITCHEN!", self.TRANSCRIPT)
        assert span is not None and span.match == "normalized"
        assert self.TRANSCRIPT[span.start : span.end] == span.text

    def test_single_word_paraphrase_still_anchors(self):
        span = locate("smoke coming from the kitchen", self.TRANSCRIPT)
        assert span is not None and span.match == "fuzzy"

    @pytest.mark.parametrize(
        "invented",
        [
            "the caller reported a weapon on scene",
            "my husband collapsed and is not breathing",
            "there are four people trapped upstairs",
        ],
    )
    def test_invented_quotes_are_rejected(self, invented):
        # This is the mechanism that stops a model manufacturing a
        # justification for a field it guessed.
        assert locate(invented, self.TRANSCRIPT) is None

    def test_quotes_too_short_to_mean_anything_are_rejected(self):
        assert locate("fire", self.TRANSCRIPT) is None
        assert locate("my", self.TRANSCRIPT) is None

    def test_term_lookup_widens_to_the_sentence(self):
        span = locate_term("smoke", self.TRANSCRIPT)
        assert span is not None
        assert span.text == self.TRANSCRIPT
        assert self.TRANSCRIPT[span.start : span.end] == span.text

    def test_term_absent_returns_nothing(self):
        assert locate_term("cuchillo", self.TRANSCRIPT) is None

    def test_resolve_falls_back_to_the_translation(self):
        original = "Hay humo saliendo de mi cocina."
        span = resolve("smoke coming from my kitchen", original, self.TRANSCRIPT)
        assert span is not None and span.source == "english"


# ---------------------------------------------------------------------------
# The recorded fixtures must still satisfy the contract
# ---------------------------------------------------------------------------
def fixture_files() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


@pytest.mark.skipif(not fixture_files(), reason="run scripts/make_expected.py first")
@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: p.stem)
class TestRecordedFixtures:
    def test_every_prediction_has_a_score_and_explicit_evidence(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        for pred in data["predictions"]:
            assert 0.0 <= pred["score"] <= 1.0, pred["label"]
            # Evidence is present or explicitly null. It is never absent, so a
            # client cannot mistake "we did not look" for "there is none".
            assert "evidence" in pred, pred["label"]

    def test_evidence_offsets_slice_back_to_the_quoted_text(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        sources = {
            "original": data["transcript_original"],
            "english": data["transcript_english"],
        }
        for pred in data["predictions"]:
            span = pred.get("evidence")
            if not span:
                continue
            text = sources[span["source"]]
            assert text[span["start"] : span["end"]] == span["text"], pred["label"]

    def test_requires_review_and_no_dispatch(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["requires_review"] is True
        assert data["suggested_action"] in {a.value for a in SuggestedAction}

    def test_abstained_fields_are_not_dressed_up(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        for pred in data["predictions"]:
            if pred["value"] == "unknown":
                # An abstention must not carry a high score, or the UI would
                # render "unknown" as though it were a confident finding.
                assert pred["score"] < 0.55, (pred["label"], pred["score"])

    def test_language_matches_the_scenario(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        expected = data["_fixture"]["language_expected"]
        if data.get("detected_language"):
            assert data["detected_language"].startswith(expected)

    def test_non_english_calls_carry_both_transcripts(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["_fixture"]["language_expected"] != "en":
            assert data["transcript_original"].strip()
            assert data["transcript_english"].strip()
            assert data["transcript_original"] != data["transcript_english"]

    def test_calibration_warning_is_carried(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("calibration_warning")


class TestCallRecordSections:
    def test_medical_call_reads_complaint_awake_and_breathing(self):
        from app.decision.rules import RuleEngine

        answers = RuleEngine().extract(
            "My name is Helen Cho. My father has chest pain. He is awake and breathing. "
            "We are at our house at 220 Cedar Avenue.",
            "",
            "en",
        ).answers
        assert answers["caller_name"].value == "Helen Cho"
        assert answers["chief_complaint"].value == "chest_pain"
        assert answers["awake"].value == "yes"
        assert answers["breathing"].value == "yes"
        assert answers["incident_type"].value == "medical"

    def test_fire_call_reads_extent_and_occupancy(self):
        from app.decision.rules import RuleEngine

        answers = RuleEngine().extract(
            "There is smoke and flames in the kitchen of the restaurant at 900 Market Street. "
            "My name is Luis Ortega. All six employees are outside. No one is injured.",
            "",
            "en",
        ).answers
        assert answers["caller_name"].value == "Luis Ortega"
        assert answers["fire_extent"].value == "one_room"
        assert answers["occupancy"].value == "commercial"
        assert answers["persons_involved"].value == 6
        assert answers["injuries"].value == "none"
        assert answers["incident_type"].value == "structure_fire"

    def test_a_medical_call_does_not_keep_a_fire_size(self):
        from app.confidence import resolve as resolve_confidence
        from app.decision.base import EngineResult, FieldAnswer
        from app.decision.rules import RuleEngine

        text = (
            "My name is Helen Cho. My father has chest pain in the kitchen. "
            "He is awake and breathing."
        )
        rules = RuleEngine().extract(text, "", "en")
        laya = EngineResult(
            answers={"incident_type": FieldAnswer("incident_type", "medical", probability=0.9)},
        )
        form = resolve_confidence(laya, rules, text, "").form
        assert form.incident_type.value == "medical"
        assert form.chief_complaint.value == "chest_pain"
        assert form.fire_extent.value == "unknown"
        assert form.occupancy.value == "unknown"
