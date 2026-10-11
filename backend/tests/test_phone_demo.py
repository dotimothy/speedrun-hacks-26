import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.live import dial_bridge
from app.live.assistant import context
from app.live.models import FieldValue, Presence, Segment, Speaker, ReviewFlag, ReviewFlagKind
from app.live.store import SessionStore
from app.main import app
from app.tts import APPROVED


@pytest.fixture
def demo(monkeypatch):
    local = SessionStore()
    monkeypatch.setattr(dial_bridge, "store", local)
    state = local.create("short-demo")
    state.phone_demo_mode = True
    state.accepted_record["incident_type"] = FieldValue(field="incident_type", value="structure_fire", presence=Presence.STATED, revision=0)
    return dial_bridge.DialBridge(call_id=state.session_id), state


def heard(state):
    state.segments.append(Segment(id=f"c{len(state.segments)}", index=len(state.segments),
                                  speaker=Speaker.CALLER, original_text="There is a fire."))


def test_demo_has_six_concise_questions_then_reassurance(demo):
    bridge, state = demo
    spoken = []
    for _ in range(8):
        heard(state)
        spoken.append(bridge._pick_phrase())
    assert spoken == ["demo_address_en", "demo_callback_en", "demo_injuries_en", "demo_safety_en", "demo_people_en", "demo_whereabouts_en", "wrap_up_partial_en", None]
    assert max(len(APPROVED[p][1]) for p in spoken[:6]) < 70
    assert len(set(spoken[:6])) == 6
    assert bridge.should_end_call()


def test_demo_skips_known_answers_and_scenario_detail(demo):
    bridge, state = demo
    state.accepted_record["location_text"] = FieldValue(field="location_text", value="123 First Street", presence=Presence.STATED, revision=0)
    heard(state)
    assert bridge._pick_phrase() == "demo_callback_en"
    fields = {q["field"] for q in context(state)["follow_up_candidates"]}
    assert "location_text" not in fields
    assert "occupancy" not in fields and "fire_observations" not in fields


def test_demo_closes_at_time_budget_after_caller_pause(demo, monkeypatch):
    bridge, state = demo
    heard(state)
    monkeypatch.setattr(settings, "dial_demo_max_s", 50)
    monkeypatch.setattr(dial_bridge.time, "perf_counter", lambda: bridge._started + 51)
    assert bridge._pick_phrase() == "wrap_up_partial_en"


def test_demo_never_overrides_safety_pause(demo, monkeypatch):
    bridge, state = demo
    heard(state)
    state.review_flags.append(ReviewFlag(id="f", kind=ReviewFlagKind.WEAPON_MENTIONED, text="Weapon reported"))
    monkeypatch.setattr(dial_bridge.time, "perf_counter", lambda: bridge._started + 100)
    assert bridge._pick_phrase() is None
    assert not bridge.should_end_call()


def test_demo_uses_short_spanish_questions(demo):
    bridge, state = demo
    state.detected_language = "es"
    heard(state)
    assert bridge._pick_phrase() == "demo_address_es"


def test_new_phone_session_uses_configured_demo_default(monkeypatch):
    local = SessionStore()
    monkeypatch.setattr(dial_bridge, "store", local)
    monkeypatch.setattr(settings, "dial_demo_mode", True)
    bridge = dial_bridge.DialBridge(call_id="new-demo")
    bridge.ensure_session()
    assert local.get(bridge.call_id).phone_demo_mode


def test_settings_switch_updates_default_and_selected_session(monkeypatch):
    monkeypatch.setattr(settings, "dial_demo_mode", False)
    monkeypatch.setattr("app.main.describe_engines", lambda: [])
    client = TestClient(app)
    state = client.post("/api/v2/sessions", json={}).json()
    try:
        result = client.post("/api/v2/phone-settings", json={"phone_demo_mode":True, "session_id":state["session_id"]})
        assert result.status_code == 200
        changed = result.json()["snapshot"]
        assert changed["phone_demo_mode"]
        assert changed["revision"] == state["revision"]
        assert changed["accepted_record"] == state["accepted_record"]
        assert client.get("/api/models").json()["phone_demo_mode"]
        assert client.post("/api/v2/phone-settings", json={"phone_demo_mode":"false"}).status_code == 422
        assert client.post("/api/v2/phone-settings", json={"phone_demo_mode":False}).json()["phone_demo_mode"] is False
    finally:
        client.delete(f"/api/v2/sessions/{state['session_id']}")


def test_switching_demo_off_restores_scenario_questions(demo):
    bridge, state = demo
    state.phone_demo_mode = False
    fields = {q["field"] for q in state.follow_up_questions}
    assert "occupancy" in fields
    heard(state)
    assert bridge._pick_phrase() == "repeat_address_en"
