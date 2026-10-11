"""Failures found by replaying the four supplied demo-calls.html scripts."""
from itertools import count

from app.confidence import resolve
from app.decision.base import EngineResult, FieldAnswer
from app.decision.rules import find_all_locations, find_name, find_extent, RuleEngine
from app.live.diffing import build_suggestions, has_correction_cue
from app.live.models import Segment, Speaker, FieldValue, Presence
from app.scenarios import extract_details


def test_location_fragments_cannot_replace_street_with_a_floor_or_wrong_home():
    assert find_all_locations("1400, 1400 Birch Court, apartment 6B.") == ["1400 Birch Court"]
    assert find_all_locations("The smoke is coming out of the third floor now.") == []
    assert find_all_locations("Birch Court and Holt.") == ["Birch Court and Holt"]
    assert find_all_locations("I live at 15 Harbor Street. That's not here. This is her house.") == []
    assert not has_correction_cue("It is not just her apartment anymore.")
    assert find_extent("Fire in her apartment. Smoke is on the third floor now, not just her apartment anymore.") == "spreading"


def test_natural_self_identification_and_spoken_age():
    assert find_name("Denise. Denise Alvarez. My phone is 555 0151.") == "Denise Alvarez"
    assert find_name("I'm Priya Shah. Call my cell.") == "Priya Shah"
    assert extract_details("She's sixty-eight.")["patient_age"] == "She's sixty-eight."
    assert "patient_age" not in extract_details("My son is here. He's eight.")


def test_observations_accumulate_before_and_after_review_without_erasing_details():
    ids = count()
    def say(text, accepted=None, pending=None):
        return build_suggestions(accepted=accepted or {}, pending=pending or [], predictions=[],
            segment=Segment(id=str(next(ids)), index=0, speaker=Speaker.CALLER, original_text=text),
            base_revision=0, next_id=lambda prefix:f"{prefix}-{next(ids)}")
    one = say("Her face is drooping and she is not talking right.")
    first = next(s for s in one.suggestions if s.field == "symptom_details")
    two = say("There's blood on her eyebrow.", pending=[first])
    second = next(s for s in two.suggestions if s.field == "symptom_details")
    assert len(second.proposed_value) == 2
    assert len(second.evidence) == 2
    record = {"symptom_details":FieldValue(field="symptom_details",value=second.proposed_value,presence=Presence.STATED,revision=1)}
    three = say("She can't move her left arm.", accepted=record)
    third = next(s for s in three.suggestions if s.field == "symptom_details")
    assert len(third.proposed_value) == 3
    assert "not talking right" in third.proposed_value[0]
    assert len(record["symptom_details"].value) == 2


def test_human_confirmed_scenario_keeps_relevant_fields_when_model_abstains():
    text = "Her eyes are open. She's breathing, but it's loud. There's blood on her eyebrow."
    result = resolve(EngineResult(answers={"incident_type":FieldAnswer("incident_type","other",0.7)}),
                     RuleEngine().extract(text,text,"en"), text,text,incident_context="medical")
    assert result.form.awake.value == "yes"
    assert result.form.breathing.value == "yes"
    assert result.form.incident_type.value == "unknown"  # not a model classification


def test_explicit_unknown_entrapment_is_not_a_positive_model_fact():
    text = "Two cars crashed. I don't know whether the person can get out."
    model = EngineResult(answers={name:FieldAnswer(name,value,0.99) for name,value in
        {"incident_type":"traffic_collision","collision_entrapment":"yes"}.items()})
    result = resolve(model,RuleEngine().extract(text,text,"en"),text,text)
    assert result.form.collision_entrapment.value == "unknown"
