"""Dial LLM mode: the protocol, the approved-phrase rule, and the socket.

The brain is a stub here, so nothing runs the pipeline or a model. The rule
under test is that only approved phrase ids ever reach the caller.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.config import settings
from app.transport.dial_llm import LlmSession, handle_frame
from app.tts import APPROVED

SECRET = "llm-test-secret"
CALL = "llm-call-1"


def phrase_text(phrase_id: str) -> str:
    return APPROVED[phrase_id][1]


def fixed_brain(phrase_id: str | None):
    async def brain(_session: LlmSession) -> str | None:
        return phrase_id

    return brain


def run(session: LlmSession, raw: str, brain=None) -> list[dict]:
    frames = asyncio.run(handle_frame(session, raw, brain or fixed_brain(None), phrase_text))
    return [json.loads(f) for f in frames]


def transcript(*turns: tuple[str, str]) -> str:
    return json.dumps(
        {
            "type": "transcript_update",
            "transcript": [{"role": role, "content": text} for role, text in turns],
        }
    )


# --- protocol --------------------------------------------------------------


def test_call_connected_records_the_call() -> None:
    session = LlmSession(call_id=CALL)
    run(session, json.dumps({"type": "call_connected", "direction": "inbound",
                             "from": "+15550001", "to": "+15550002",
                             "instruction": "intake", "language": "en-US"}))
    assert (session.direction, session.caller, session.callee) == ("inbound", "+15550001", "+15550002")
    assert session.instruction == "intake"
    assert session.language == "en-US"


def test_transcript_updates_replace_the_snapshot() -> None:
    session = LlmSession(call_id=CALL)
    run(session, transcript(("user", "There is smoke")))
    run(session, transcript(("user", "There is smoke"), ("agent", "Where?"), ("user", "Kitchen")))
    assert [t["content"] for t in session.transcript] == ["There is smoke", "Where?", "Kitchen"]
    assert session.caller_text() == "There is smoke Kitchen"


def test_response_required_answers_with_the_same_response_id() -> None:
    session = LlmSession(call_id=CALL)
    raw = json.dumps({"type": "response_required", "response_id": "r-7",
                      "transcript": [{"role": "user", "content": "hi"}]})
    (frame,) = run(session, raw, brain=fixed_brain("what_happening_en"))
    assert frame == {"type": "response", "response_id": "r-7",
                     "content": phrase_text("what_happening_en"), "content_complete": True}


def test_no_phrase_means_no_response_frame() -> None:
    session = LlmSession(call_id=CALL)
    raw = json.dumps({"type": "response_required", "response_id": "r-8", "transcript": []})
    assert run(session, raw, brain=fixed_brain(None)) == []


def test_reminder_gets_a_holding_line() -> None:
    session = LlmSession(call_id=CALL)
    (frame,) = run(session, json.dumps({"type": "reminder_required", "response_id": "r-9",
                                        "transcript": []}))
    assert frame["content"] == phrase_text("stay_on_line_en")
    assert frame["response_id"] == "r-9"


def test_ping_pong_is_echoed_unchanged() -> None:
    session = LlmSession(call_id=CALL)
    raw = json.dumps({"type": "ping_pong", "timestamp": 1749480000000})
    assert asyncio.run(handle_frame(session, raw, fixed_brain(None), phrase_text)) == [raw]


def test_call_ended_stops_the_session() -> None:
    session = LlmSession(call_id=CALL)
    run(session, json.dumps({"type": "call_ended"}))
    assert session.ended


def test_bad_json_is_dropped() -> None:
    assert run(LlmSession(call_id=CALL), "{not json") == []


def test_only_approved_phrases_are_ever_spoken() -> None:
    # Whatever the brain returns, a response carries the approved words for the
    # phrase id, never the brain's own text.
    session = LlmSession(call_id=CALL)
    raw = json.dumps({"type": "response_required", "response_id": "r-10", "transcript": []})
    (frame,) = run(session, raw, brain=fixed_brain("ask_injuries_en"))
    assert frame["content"] == APPROVED["ask_injuries_en"][1]


# --- socket ----------------------------------------------------------------


def sign(call_id: str = CALL, secret: str = SECRET) -> str:
    stamp = int(time.time())
    digest = hmac.new(secret.encode(), f"{stamp}.{call_id}".encode(), hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


@pytest.fixture
def llm_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dial_llm_signing_secret", SECRET)

    async def brain(_session: LlmSession) -> str | None:
        return "ask_safe_en"

    monkeypatch.setattr(main, "_llm_brain", brain)


def test_llm_socket_refuses_without_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dial_llm_signing_secret", "")
    client = TestClient(main.app)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/dispatchiq/llm/{CALL}", headers={"x-dial-signature": sign()}
    ):
        pass


def test_llm_socket_refuses_a_bad_signature(llm_enabled: None) -> None:
    client = TestClient(main.app)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/dispatchiq/llm/{CALL}", headers={"x-dial-signature": sign(secret="wrong")}
    ):
        pass


def test_llm_socket_answers_a_turn(llm_enabled: None) -> None:
    client = TestClient(main.app)
    with client.websocket_connect(
        f"/dispatchiq/llm/{CALL}", headers={"x-dial-signature": sign()}
    ) as ws:
        ws.send_text(json.dumps({"type": "response_required", "response_id": "r-1", "transcript": []}))
        reply = json.loads(ws.receive_text())
        assert reply["type"] == "response"
        assert reply["response_id"] == "r-1"
        assert reply["content"] == APPROVED["ask_safe_en"][1]
        ws.send_text(json.dumps({"type": "call_ended"}))
