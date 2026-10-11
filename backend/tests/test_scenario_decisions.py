"""Exercise model answers through policy, adaptive questions, review and report."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.confidence import resolve
from app.decision.base import EngineResult, FieldAnswer
from app.decision.rules import RuleEngine
from app.decision.scenario_tasks import DECISIONS
from app.live import router
from app.live.diffing import build_suggestions
from app.live.models import FieldValue, Presence, Segment, Speaker, SessionSnapshot, Suggestion, UpdateKind
from app.main import app

CASES = json.loads((Path(__file__).parents[1] / "evals/scenario_decision_cases.json").read_text())


def model_result(values, probability=0.99):
    return EngineResult(answers={name: FieldAnswer(label=name, value=value, probability=probability)
                                for name, value in values.items()}, engine="test_model", model="synthetic")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_scenario_model_answers_survive_policy_and_human_review_over_http(case, monkeypatch):
    class Model:
        name = "test_model"
        is_local = True
        calls = 0

        def describe(self):
            return {"inference_location": "test fixture"}

        def extract(self, original, english, language):
            self.calls += 1
            return model_result(case["expected"])

    model = Model()
    monkeypatch.setattr(router, "get_engine", lambda name=None: model)
    client = TestClient(app)
    state = client.post("/api/v2/sessions", json={}).json()
    base = f"/api/v2/sessions/{state['session_id']}"
    try:
        response = client.post(base + "/segments", json={"text": case["text"], "speaker": "caller"})
        assert response.status_code == 200, response.text
        state = response.json()["snapshot"]
        assert state["decision_focus"]["scenario"] == case["expected"]["incident_type"]
        if "followup" in case:
            assert case["followup"] in state["relevant_fields"]
            question = next(q for q in state["follow_up_questions"] if q["field"] == case["followup"])
            assert "review interpretation" in question["reason"]
        for field, value in case["expected"].items():
            suggestions = [s for s in state["suggestions"] if s["field"] == field and s["status"] == "pending"]
            if value == "unknown":
                assert not suggestions
                assert field in {q["field"] for q in state["follow_up_questions"]}
                continue
            assert len(suggestions) == 1
            suggestion = suggestions[0]
            assert suggestion["proposed_value"] == value
            assert field not in state["accepted_record"]
            if field in DECISIONS:
                assert suggestion["evidence"]
                assert suggestion["raw_model"]["probability"] == 0.99
                assert f"{DECISIONS[field][1]}: {value.replace('_', ' ')}." not in state["narrative"]["text"]
            review = client.post(base + f"/suggestions/{suggestion['id']}/accept", json={
                "base_revision": state["revision"], "operation_id": "accept-" + field})
            assert review.status_code == 200, review.text
            state = review.json()["snapshot"]
            assert state["accepted_record"][field]["value"] == value
            if field in DECISIONS:
                row = next(r for r in state["decision_focus"]["decisions"] if r["field"] == field)
                assert row["status"] == "accepted"
                assert f"{DECISIONS[field][1]}: {value.replace('_', ' ')}." in state["narrative"]["text"]
        publication = client.post(base + "/publish", json={"base_revision": state["revision"], "operation_id": "publish"})
        assert publication.status_code == 201
        assert model.calls == 1  # review, rendering and publication never re-infer
    finally:
        client.delete(base)


@pytest.mark.parametrize("value,probability,text", [
    ("people_inside", 0.99, "There is a fire."),
    ("invented", 0.99, "Someone is inside."),
    ("people_inside", 0.1, "Someone is inside."),
])
def test_unsupported_or_low_probability_scenario_answers_abstain(value, probability, text):
    result = resolve(model_result({"incident_type": "structure_fire", "fire_occupants": value}, probability),
                     EngineResult(answers={}), text, text)
    assert result.form.fire_occupants.value == "unknown"


def test_out_of_scenario_decision_is_not_offered():
    text = "There is a medical emergency. Everyone is outside."
    result = resolve(model_result({"incident_type": "medical", "fire_occupants": "all_out"}),
                     EngineResult(answers={}), text, text)
    assert result.form.fire_occupants.value == "unknown"
    assert next(p for p in result.predictions if p.label == "fire_occupants").value == "unknown"


def test_pattern_notes_never_supply_model_decisions():
    for case in CASES:
        answers = RuleEngine().extract(case["text"], case["text"], "en").answers
        assert not set(answers).intersection(DECISIONS)


def test_correction_uses_new_sentence_and_keeps_accepted_value_until_review():
    old = "There is a fire. A person is still inside."
    new = "Correction: everyone is outside and accounted for."
    result = resolve(model_result({"incident_type": "structure_fire", "fire_occupants": "all_out"}),
                     EngineResult(answers={}), old + " " + new, old + " " + new)
    record = {"fire_occupants": FieldValue(field="fire_occupants", value="people_inside", revision=1)}
    outcome = build_suggestions(accepted=record, pending=[], predictions=result.predictions,
        segment=Segment(id="new", index=1, speaker=Speaker.CALLER, original_text=new),
        base_revision=1, next_id=lambda prefix: prefix)
    suggestion = next(s for s in outcome.suggestions if s.field == "fire_occupants")
    assert suggestion.kind == UpdateKind.CORRECTION
    assert suggestion.evidence[0].text == new
    assert record["fire_occupants"].value == "people_inside"
    unrelated = build_suggestions(accepted={}, pending=[], predictions=result.predictions,
        segment=Segment(id="later", index=2, speaker=Speaker.CALLER, original_text="My phone is 555-0147."),
        base_revision=1, next_id=lambda prefix: prefix)
    assert not any(s.field == "fire_occupants" for s in unrelated.suggestions)


def test_conflict_disables_branch_until_resolved_and_negative_changes_the_plan():
    state = SessionSnapshot(session_id="branch", accepted_record={
        name: FieldValue(field=name, value=value, presence=Presence.STATED, revision=1) for name, value in
        {"incident_type":"traffic_collision", "collision_entrapment":"yes"}.items()})
    assert "trapped_vehicle_location" in state.relevant_fields
    state.suggestions.append(Suggestion(id="conflict", field="collision_entrapment", kind=UpdateKind.CONFLICT, base_revision=1))
    assert "trapped_vehicle_location" not in state.relevant_fields
    assert next(r for r in state.decision_focus["decisions"] if r["field"] == "collision_entrapment")["status"] == "conflict"
    state.suggestions.clear()
    state.accepted_record["collision_entrapment"].value = "no"
    assert "trapped_vehicle_location" not in state.relevant_fields


def test_new_questions_reach_both_model_adapters():
    from app.decision.laya_model import LayaEngine
    from app.decision.openai_decisions import questions
    seen = {}
    class Router:
        def predict(self, state, questions):
            seen.update(questions)
            return {"answers": {name: {"type":"choice", "choice":"unknown", "answer_confidence":0.9}
                                for name in DECISIONS}}
    engine = LayaEngine()
    engine._router = Router()
    result = engine.extract("Synthetic transcript", "Synthetic transcript", "en")
    assert set(DECISIONS) <= result.answers.keys()
    assert all("unknown" in seen[name]["criteria"] for name in DECISIONS)
    hosted = {q["name"]: q for q in questions()}
    assert all({c["value"] for c in hosted[name]["choices"]} == set(spec[3]) | {"unknown"}
               for name, spec in DECISIONS.items())
