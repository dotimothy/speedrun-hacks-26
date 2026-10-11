from fastapi.testclient import TestClient

from app.decision.base import EngineResult
from app.live import router
from app.main import app


def test_switch_engine_applies_to_next_input_without_rewriting_record(monkeypatch):
    calls = []

    class Engine:
        is_local = True

        def __init__(self, name): self.name = name or "laya"
        def ready(self): return True
        def describe(self): return {"inference_location":"test"}
        def extract(self, original, english, language):
            calls.append(self.name)
            return EngineResult(answers={}, engine=self.name)

    monkeypatch.setattr(router, "available", lambda: ["laya", "rules"])
    monkeypatch.setattr(router, "get_engine", lambda name=None: Engine(name))
    client = TestClient(app)
    state = client.post("/api/v2/sessions", json={}).json()
    base = f"/api/v2/sessions/{state['session_id']}"
    try:
        record = client.post(base + "/fields/caller_name", json={
            "value":"Example Caller", "presence":"stated", "operation_id":"name"}).json()["snapshot"]
        changed = client.post(base + "/settings", json={"decision_engine":"rules"})
        assert changed.status_code == 200
        state = changed.json()["snapshot"]
        assert state["decision_engine"] == "rules"
        assert state["revision"] == record["revision"]
        assert state["accepted_record"] == record["accepted_record"]
        assert state["event_seq"] > record["event_seq"]
        assert calls == []
        response = client.post(base + "/segments", json={"text":"There is smoke.", "speaker":"caller"})
        assert response.status_code == 200
        assert response.json()["snapshot"]["diagnostics"]["engine"]["decision"] == "rules"
        assert calls == ["rules"]
        client.post(base + "/settings", json={"decision_engine":None}).raise_for_status()
        client.post(base + "/segments", json={"text":"I am outside.", "speaker":"caller"}).raise_for_status()
        assert calls == ["rules", "laya"]
    finally:
        client.delete(base)


def test_disabled_and_unready_models_leave_selection_unchanged(monkeypatch):
    monkeypatch.setattr(router, "available", lambda: ["rules", "laya"])
    class Unready:
        def ready(self): return False
    monkeypatch.setattr(router, "get_engine", lambda name=None: Unready())
    client = TestClient(app)
    state = client.post("/api/v2/sessions", json={}).json()
    base = f"/api/v2/sessions/{state['session_id']}"
    try:
        for payload in ({"decision_engine":"openai"}, {"decision_engine":"invalid"}, {"decision_engine":123}, {}):
            assert client.post(base + "/settings", json=payload).status_code == 422
        assert client.post(base + "/settings", json={"decision_engine":"laya"}).status_code == 409
        after = client.get(base).json()
        assert after["decision_engine"] is None
        assert after["event_seq"] == state["event_seq"]
    finally:
        client.delete(base)
