import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.decision.rules import RuleEngine
from app.live import assistant, router
from app.live.models import Presence, Segment, SessionSnapshot, Speaker
from app.live.store import store
from app.main import app


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "assistant_enabled", True)
    monkeypatch.setattr(settings, "allow_cloud_inference", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(router, "get_engine", lambda name=None: RuleEngine())


def snapshot():
    return SessionSnapshot(session_id="assist-test", segments=[Segment(
        id="caller-1", index=0, speaker=Speaker.CALLER, original_text="My father has chest pain.")])


def content(payload):
    return {"summary":"The caller reports that their father has chest pain. The location is not yet known.",
            "follow_up_question":"What is the address where your father is?", "target_field":"location_text",
            "source_segment_ids":[payload["caller_statements"][0]["id"]]}


def test_post_decision_endpoint_caches_draft_without_accepting_or_publishing(enabled, monkeypatch):
    requests = []
    def complete(payload):
        requests.append(payload)
        return content(payload)
    monkeypatch.setattr(assistant, "_complete", complete)
    client = TestClient(app)
    state = client.post("/api/v2/sessions", json={}).json()
    base = f"/api/v2/sessions/{state['session_id']}"
    try:
        before = client.post(base + "/segments", json={"text":"My father has chest pain.", "speaker":"caller"}).json()["snapshot"]
        response = client.post(base + "/assist")
        assert response.status_code == 200, response.text
        after = response.json()["snapshot"]
        assert after["assistant"]["status"] == "ready"
        assert after["assistant"]["target_field"] == "location_text"
        assert requests[0]["pending_decisions"]
        assert requests[0]["decision_focus"]["scenario"] == "medical"
        assert after["accepted_record"] == before["accepted_record"]
        assert after["narrative"] == before["narrative"]
        assert after["revision"] == before["revision"]
        assert after["published"] == []
        assert client.post(base + "/assist").json()["cached"]
        assert len(requests) == 1
    finally:
        client.delete(base)


def test_old_generation_cannot_overwrite_new_review(enabled, monkeypatch):
    client = TestClient(app)
    state = client.post("/api/v2/sessions", json={}).json()
    base = f"/api/v2/sessions/{state['session_id']}"
    def complete(payload):
        store.set_field_directly(state["session_id"], "location_text", value="77 Willow Lane",
                                presence=Presence.STATED, operation_id="concurrent-review")
        return content(payload)
    monkeypatch.setattr(assistant, "_complete", complete)
    try:
        client.post(base + "/segments", json={"text":"My father has chest pain.", "speaker":"caller"}).raise_for_status()
        result = client.post(base + "/assist").json()
        assert result["stale"] is True
        assert result["snapshot"]["assistant"] is None
        assert result["snapshot"]["accepted_record"]["location_text"]["value"] == "77 Willow Lane"
    finally:
        client.delete(base)


@pytest.mark.parametrize("change", [
    {"target_field":"awake"},
    {"source_segment_ids":["invented"]},
    {"follow_up_question":"What is the address? Is anyone hurt?"},
    {"summary":""},
    {"follow_up_question":None, "target_field":None},
])
def test_invalid_drafts_do_not_become_assistance(enabled, monkeypatch, change):
    monkeypatch.setattr(assistant, "_complete", lambda p: {**content(p), **change})
    result = assistant.generate(snapshot())
    assert result.status == "error"
    assert result.summary == "" and result.follow_up_question is None


def test_cloud_disabled_and_empty_call_never_contact_provider(enabled, monkeypatch):
    def fail(_): raise AssertionError("Provider must not be called")
    monkeypatch.setattr(assistant, "_complete", fail)
    monkeypatch.setattr(settings, "allow_cloud_inference", False)
    assert assistant.generate(snapshot()).status == "unavailable"
    monkeypatch.setattr(settings, "allow_cloud_inference", True)
    assert assistant.generate(SessionSnapshot(session_id="empty")).status == "unavailable"


def test_provider_error_is_sanitized(enabled, monkeypatch):
    def fail(_): raise RuntimeError("secret-key and private transcript")
    monkeypatch.setattr(assistant, "_complete", fail)
    result = assistant.generate(snapshot())
    assert result.status == "error"
    assert "secret-key" not in result.model_dump_json()


def test_operator_speech_is_not_caller_evidence(enabled, monkeypatch):
    state = snapshot()
    state.segments.append(Segment(id="operator",index=1,speaker=Speaker.OPERATOR,original_text="Is the address 88 Other Street?"))
    def complete(payload):
        assert "88 Other" not in json.dumps(payload["caller_statements"])
        assert payload["previous_operator_questions"] == ["Is the address 88 Other Street?"]
        return content(payload)
    monkeypatch.setattr(assistant, "_complete", complete)
    assert assistant.generate(state).status == "ready"


def test_provider_contract_requires_structured_output_and_handles_refusal(enabled, monkeypatch):
    refused = False
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.chat = SimpleNamespace(completions=self)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            assert kwargs["model"] == settings.assistant_model
            schema = kwargs["response_format"]["json_schema"]
            assert schema["strict"] is True
            assert "location_text" in schema["schema"]["properties"]["target_field"]["enum"]
            payload = json.loads(kwargs["messages"][1]["content"])
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",message=SimpleNamespace(
                refusal="refused" if refused else None,content=json.dumps(content(payload))))])
    monkeypatch.setattr("openai.OpenAI", Client)
    assert assistant.generate(snapshot()).status == "ready"
    refused = True
    assert assistant.generate(snapshot()).status == "error"


@pytest.fixture
def phone(enabled, monkeypatch):
    from app.live import dial_bridge
    from app.live.store import SessionStore
    local_store = SessionStore()
    monkeypatch.setattr(dial_bridge, "store", local_store)
    monkeypatch.setattr(dial_bridge, "get_engine", lambda name=None: RuleEngine())
    local_store.create("phone-assistant")
    monkeypatch.setattr(assistant, "_complete", content)
    monkeypatch.setattr(dial_bridge, "synthesize_question", lambda text, language: b"rendered-audio")
    original = SimpleNamespace(text="My father has chest pain.", language="en",
                               language_probability=1.0, word_confidence=0.98)
    monkeypatch.setattr(dial_bridge.whisper_engine, "transcribe_and_translate", lambda *args: (original, None))
    return dial_bridge.DialBridge(call_id="phone-assistant"), local_store


def test_phone_speaks_generated_question_after_extraction_and_records_exact_text(phone):
    from app.transport.dial_call import GeneratedReply
    bridge, local_store = phone
    reply = bridge._process_chunk(bytes(64000))
    state = local_store.get(bridge.call_id)
    assert isinstance(reply, GeneratedReply)
    assert state.pending  # extraction happened before assistance
    assert state.assistant.status == "ready"
    assert reply.text == state.assistant.follow_up_question
    assert state.segments[-1].speaker == Speaker.OPERATOR
    assert state.segments[-1].original_text == reply.text
    assert state.revision == 0 and not state.published
    assert bridge._spoken["repeat_address_en"][1] == 1
    # An answer retires that question even if extraction leaves the field unknown.
    bridge._process_chunk(bytes(64000))
    assert bridge._spoken["repeat_address_en"][1] == 1


@pytest.mark.parametrize("failure", ["provider", "speech", "disabled"])
def test_phone_falls_back_to_fixed_question(phone, monkeypatch, failure):
    from app.tts import APPROVED
    bridge, local_store = phone
    if failure == "provider":
        monkeypatch.setattr(assistant, "_complete", lambda _: {})
    elif failure == "speech":
        monkeypatch.setattr("app.live.dial_bridge.synthesize_question", lambda *args: None)
    else:
        monkeypatch.setattr(settings, "assistant_enabled", False)
    reply = bridge._process_chunk(bytes(64000))
    assert reply == "repeat_address_en"
    assert local_store.get(bridge.call_id).segments[-1].original_text == APPROVED[reply][1]


@pytest.mark.parametrize("blocked", ["flag", "language", "changed_review"])
def test_generated_voice_respects_safety_language_and_freshness(phone, monkeypatch, blocked):
    from app.live.models import ReviewFlag, ReviewFlagKind
    bridge, local_store = phone
    state = local_store.get(bridge.call_id)
    state.segments = snapshot().segments
    if blocked == "flag":
        state.review_flags.append(ReviewFlag(id="flag-1", kind=ReviewFlagKind.WEAPON_MENTIONED,
                                            text="Weapon mentioned"))
    elif blocked == "language":
        state.detected_language = "fr"
    else:
        def synthesize(*args):
            local_store.set_field_directly(bridge.call_id, "location_text", value="77 Willow Lane",
                                          presence=Presence.STATED, operation_id="review-during-tts")
            return b"audio"
        monkeypatch.setattr("app.live.dial_bridge.synthesize_question", synthesize)
    before = len(state.segments)
    assert bridge._reply_after_decision() is None
    assert len(state.segments) == before


def test_concurrent_assistant_requests_share_one_completion(enabled, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.live.store import SessionStore
    local_store = SessionStore()
    local_store.create("same-call").segments = snapshot().segments
    entered, release = Event(), Event()
    calls = []
    def complete(payload):
        calls.append(payload)
        entered.set()
        assert release.wait(3)
        return content(payload)
    monkeypatch.setattr(assistant, "_complete", complete)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(assistant.refresh, "same-call", local_store)
        assert entered.wait(3)
        second = pool.submit(assistant.refresh, "same-call", local_store)
        release.set()
        assert first.result()["stale"] is False
        assert second.result()["cached"] is True
    assert len(calls) == 1
