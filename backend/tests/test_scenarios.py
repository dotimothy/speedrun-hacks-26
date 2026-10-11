"""Detailed reports and adaptive questions without a model or a live call."""
from itertools import count

import pytest
from fastapi.testclient import TestClient

from app.decision.rules import RuleEngine
from app.live import router
from app.live.diffing import build_suggestions
from app.live.followup import follow_up_questions
from app.live.models import FieldValue, Presence, Segment, SessionSnapshot, Speaker, Suggestion, UpdateKind
from app.live.narrative import render
from app.main import app
from app.scenarios import SCENARIO_FIELDS, extract_details
from app.tts import APPROVED


def accepted(name, value, presence=Presence.STATED):
    return FieldValue(field=name, value=value, presence=presence, revision=1)


def snapshot(incident="medical"):
    return SessionSnapshot(session_id="scenario", accepted_record={
        name: accepted(name, value) for name, value in {
            "incident_type": incident, "location_text": "220 Cedar Street",
            "callback_number": "555-0182", "injuries": "none", "caller_safe": "yes",
        }.items()
    })


@pytest.mark.parametrize("incident", SCENARIO_FIELDS)
@pytest.mark.parametrize("language", ["en", "es"])
def test_each_scenario_has_approved_bilingual_questions(incident, language):
    state = snapshot(incident)
    state.detected_language = language
    questions = follow_up_questions(state)
    assert set(SCENARIO_FIELDS[incident]) <= {q["field"] for q in questions}
    assert all(q["phrase_id"].endswith("_" + language) and q["phrase_id"] in APPROVED for q in questions)


def test_fire_does_not_ask_medical_or_vehicle_questions():
    questions = follow_up_questions(snapshot("structure_fire"))
    assert {q["field"] for q in questions}.isdisjoint({"chief_complaint", "patient_age", "vehicle_details"})


def test_pending_answer_and_stated_absence_do_not_get_asked_again():
    state = snapshot()
    state.accepted_record["medical_history"] = accepted("medical_history", None, Presence.STATED_ABSENT)
    state.suggestions.append(Suggestion(id="age", field="patient_age", proposed_value="67 years old",
                                        kind=UpdateKind.ADDITION, base_revision=0))
    fields = {q["field"] for q in follow_up_questions(state)}
    assert "patient_age" not in fields and "medical_history" not in fields


def test_conflicting_incident_does_not_choose_a_scenario():
    state = snapshot()
    state.suggestions.append(Suggestion(id="conflict", field="incident_type", kind=UpdateKind.CONFLICT, base_revision=0))
    questions = follow_up_questions(state)
    assert [q["field"] for q in questions] == ["incident_type"]
    assert "Conflicting" in questions[0]["reason"]


def test_unknown_and_false_are_different():
    state = snapshot()
    state.accepted_record["awake"] = accepted("awake", False)
    state.accepted_record["breathing"] = accepted("breathing", "unknown", Presence.UNKNOWN)
    fields = {q["field"] for q in follow_up_questions(state)}
    assert "awake" not in fields and "breathing" in fields


def test_details_keep_negation_and_uncertainty_verbatim():
    text = "I do not know whether anyone is still inside. The northbound lane is not blocked."
    details = extract_details(text)
    assert details["people_accounted_for"] == "I do not know whether anyone is still inside."
    assert details["road_conditions"] == "The northbound lane is not blocked."


def test_spanish_details_are_not_dropped():
    details = extract_details("Tiene 67 años. Tiene antecedentes de asma. La entrada está al lado del portón.")
    assert {"patient_age", "medical_history", "access_notes"} <= details.keys()


def test_report_only_contains_accepted_details_and_relevant_gaps():
    state = snapshot()
    state.accepted_record["patient_age"] = accepted("patient_age", "He is 67 years old.")
    state.suggestions.append(Suggestion(id="new", field="medical_history", proposed_value="asthma",
                                        kind=UpdateKind.ADDITION, base_revision=1))
    report = render(state.accepted_record)
    assert "67 years old" in report
    assert "asthma" not in report
    assert "Relevant history reported: not stated" in report
    assert "Fire extent" not in report
    assert "Draft" in report


def test_detail_correction_is_reviewable_and_does_not_overwrite():
    record = {"access_notes": accepted("access_notes", "The entrance is behind the building.")}
    ids = count()
    outcome = build_suggestions(accepted=record, pending=[], predictions=[],
        segment=Segment(id="seg", index=1, speaker=Speaker.CALLER,
                        original_text="Correction: the entrance is on the east side."),
        base_revision=1, next_id=lambda prefix: f"{prefix}-{next(ids)}")
    suggestion = next(s for s in outcome.suggestions if s.field == "access_notes")
    assert suggestion.kind == UpdateKind.CORRECTION
    assert suggestion.evidence[0].method.value == "rule_verbatim"
    assert record["access_notes"].value == "The entrance is behind the building."


def test_multi_turn_report_over_http(monkeypatch):
    monkeypatch.setattr(router, "get_engine", lambda name=None: RuleEngine())
    client = TestClient(app)
    state = client.post("/api/v2/sessions", json={}).json()
    base = f"/api/v2/sessions/{state['session_id']}"
    try:
        def say(text, speaker="caller"):
            response = client.post(base + "/segments", json={"text": text, "speaker": speaker})
            assert response.status_code == 200, response.text
            return response.json()["snapshot"]

        state = say("My father has chest pain at 220 Cedar Street. My callback is 555-0182. He is awake and breathing.")
        assert "patient_age" in {q["field"] for q in state["follow_up_questions"]}
        state = say("He is 67 years old. He has a history of asthma. The entrance is beside the courtyard.")
        assert "patient_age" not in {q["field"] for q in state["follow_up_questions"]}
        assert "67 years old" not in state["narrative"]["text"]
        for suggestion in state["suggestions"]:
            if suggestion["status"] != "pending":
                continue
            response = client.post(base + f"/suggestions/{suggestion['id']}/accept", json={
                "base_revision": state["revision"], "operation_id": "accept-" + suggestion["id"]})
            assert response.status_code == 200, response.text
            state = response.json()["snapshot"]
        assert "67 years old" in state["narrative"]["text"]
        assert "asthma" in state["narrative"]["text"]
        prior = len(state["suggestions"])
        state = say("Was he 80 years old?", speaker="operator")
        assert len(state["suggestions"]) == prior
        publication = client.post(base + "/publish", json={"base_revision": state["revision"], "operation_id": "publish"})
        assert publication.status_code == 201
        assert "67 years old" in publication.json()["published"]["narrative"]
    finally:
        client.delete(base)


def test_live_phone_asks_a_scenario_question(monkeypatch):
    from app.live import dial_bridge
    from app.live.store import SessionStore
    storage = SessionStore()
    state = storage.create("phone-test")
    state.accepted_record = snapshot("traffic_collision").accepted_record
    monkeypatch.setattr(dial_bridge, "store", storage)
    bridge = dial_bridge.DialBridge(call_id="phone-test")
    assert bridge._pick_phrase() == "ask_vehicle_details_en"
    state.accepted_record["vehicle_details"] = accepted("vehicle_details", "Two cars.")
    assert bridge._pick_phrase() == "ask_persons_involved_en"


def test_old_details_are_not_attributed_to_new_utterances():
    from app.schemas import Prediction
    outcome = build_suggestions(accepted={}, pending=[],
        predictions=[Prediction(label="patient_age", value="67 years old", score=0.8)],
        segment=Segment(id="later", index=3, speaker=Speaker.CALLER, original_text="My callback is 555-0199."),
        base_revision=0, next_id=lambda prefix: prefix)
    assert not any(s.field == "patient_age" for s in outcome.suggestions)
