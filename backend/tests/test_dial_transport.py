"""The Dial transport's invariants: signatures, codecs and frame handling.

No network and no Dial account are needed. The socket test drives the real
route with Starlette's test client, using a secret we choose.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.config import settings
from app.transport.audio import (
    mulaw_to_pcm16,
    pcm16_to_mulaw,
    pcm16_to_wav,
    resample_pcm16,
)
from app.transport.dial import DialSession, parse_signature, verify_signature

SECRET = "test-signing-secret"
CALL = "cmv-test-call"


def sign(call_id: str = CALL, secret: str = SECRET, t: int | None = None) -> str:
    stamp = int(time.time()) if t is None else t
    digest = hmac.new(
        secret.encode(), f"{stamp}.{call_id}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={stamp},v1={digest}"


def media(data: bytes) -> str:
    return json.dumps({"type": "media", "payload": base64.b64encode(data).decode()})


# --- G.711 -----------------------------------------------------------------


def test_mulaw_reference_values() -> None:
    # Standard G.711 anchors: 0xFF is silence, 0x00 is the most negative code.
    assert mulaw_to_pcm16(b"\xff") == (0).to_bytes(2, "little", signed=True)
    assert mulaw_to_pcm16(b"\x00") == (-32124).to_bytes(2, "little", signed=True)
    assert mulaw_to_pcm16(b"\x80") == (32124).to_bytes(2, "little", signed=True)


def test_mulaw_decode_encode_is_stable() -> None:
    # mu-law has two codes for zero (0x7F and 0xFF). Decoding then encoding
    # may pick the canonical one, but the decoded level must not move.
    every_code = bytes(range(256))
    once = mulaw_to_pcm16(every_code)
    twice = mulaw_to_pcm16(pcm16_to_mulaw(once))
    assert twice == once


# --- resampling and WAV ----------------------------------------------------


def test_resample_8k_to_16k_doubles_length_and_keeps_level() -> None:
    constant = (1000).to_bytes(2, "little", signed=True) * 10
    out = resample_pcm16(constant, 8000, 16000)
    assert len(out) == 20 * 2
    samples = [int.from_bytes(out[i : i + 2], "little", signed=True) for i in range(0, len(out), 2)]
    assert set(samples) == {1000}


def test_resample_same_rate_is_passthrough() -> None:
    pcm = bytes(range(32))
    assert resample_pcm16(pcm, 16000, 16000) is pcm


def test_wav_wrapper_is_16k_mono() -> None:
    import io
    import wave

    wav = pcm16_to_wav(b"\x00\x00" * 16000)
    with wave.open(io.BytesIO(wav)) as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate()) == (1, 2, 16000)
        assert f.getnframes() == 16000


# --- signatures ------------------------------------------------------------


def test_valid_signature_is_accepted() -> None:
    assert verify_signature(sign(), CALL, SECRET)


@pytest.mark.parametrize(
    "header, call_id, secret",
    [
        (sign(secret="wrong"), CALL, SECRET),  # signed with another secret
        (sign(call_id="other"), CALL, SECRET),  # signature bound to a different call
        (sign(), CALL, ""),  # no secret configured
    ],
)
def test_bad_signatures_are_rejected(header: str, call_id: str, secret: str) -> None:
    assert not verify_signature(header, call_id, secret)


def test_stale_signature_is_rejected() -> None:
    old = int(time.time()) - 3600
    assert not verify_signature(sign(t=old), CALL, SECRET, tolerance_s=300)


def test_replayed_signature_outside_tolerance_is_rejected_even_if_valid() -> None:
    stamp = 1_000_000
    header = sign(t=stamp)
    assert verify_signature(header, CALL, SECRET, now=stamp + 10, tolerance_s=300)
    assert not verify_signature(header, CALL, SECRET, now=stamp + 900, tolerance_s=300)


@pytest.mark.parametrize("header", [None, "", "garbage", "t=abc,v1=ff", "v1=ff"])
def test_malformed_headers_do_not_parse(header: str | None) -> None:
    assert parse_signature(header) is None


# --- session frames --------------------------------------------------------


def connected(inbound: str = "mulaw_8000", outbound: str = "mulaw_8000") -> str:
    return json.dumps(
        {
            "type": "call_connected",
            "audioInboundFormat": inbound,
            "audioOutboundFormat": outbound,
            "reconnect": False,
        }
    )


def test_media_is_buffered_as_16k_pcm() -> None:
    session = DialSession(call_id=CALL)
    session.on_message(connected())
    # One 20 ms mu-law frame: 160 samples at 8 kHz.
    session.on_message(media(b"\xff" * 160))
    assert len(session.pcm16k) == 320 * 2  # 320 samples at 16 kHz
    assert session.buffered_seconds() == pytest.approx(0.02)


def test_echo_returns_inbound_payload_when_formats_match() -> None:
    session = DialSession(call_id=CALL, echo=True)
    session.on_message(connected())
    frame = media(b"\x7f" * 160)
    (reply,) = session.on_message(frame)
    assert json.loads(reply) == json.loads(frame)


def test_no_echo_when_formats_differ() -> None:
    session = DialSession(call_id=CALL, echo=True)
    session.on_message(connected(inbound="mulaw_8000", outbound="l16_16000"))
    assert session.on_message(media(b"\x7f" * 160)) == []


def test_ping_pong_is_echoed_unchanged() -> None:
    session = DialSession(call_id=CALL)
    raw = json.dumps({"type": "ping_pong", "ts": 42})
    assert session.on_message(raw) == [raw]


def test_call_ended_stops_the_session() -> None:
    session = DialSession(call_id=CALL)
    session.on_message(json.dumps({"type": "call_ended"}))
    assert session.ended


def test_unsupported_format_ends_the_call_with_end_call() -> None:
    session = DialSession(call_id=CALL)
    (reply,) = session.on_message(connected(inbound="alaw_8000"))
    assert json.loads(reply) == {"type": "end_call"}
    assert session.ended


def test_bad_json_and_bad_base64_are_dropped_not_fatal() -> None:
    session = DialSession(call_id=CALL)
    session.on_message(connected())
    assert session.on_message("{not json") == []
    assert session.on_message(json.dumps({"type": "media", "payload": "!!!"})) == []
    assert len(session.pcm16k) == 0


# --- WebSocket route -------------------------------------------------------


@pytest.fixture
def dial_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dial_signing_secret", SECRET)


def test_socket_refuses_when_no_secret_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dial_signing_secret", "")
    client = TestClient(main.app)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/dial/{CALL}", headers={"x-dial-signature": sign()}
    ):
        pass


def test_socket_refuses_bad_signature(dial_enabled: None) -> None:
    client = TestClient(main.app)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/dial/{CALL}", headers={"x-dial-signature": sign(secret="wrong")}
    ):
        pass


def test_socket_echoes_caller_audio_for_a_valid_call(dial_enabled: None) -> None:
    client = TestClient(main.app)
    frame = media(b"\x7f" * 160)
    with client.websocket_connect(
        f"/dial/{CALL}", headers={"x-dial-signature": sign()}
    ) as ws:
        ws.send_text(connected())
        ws.send_text(frame)
        assert json.loads(ws.receive_text()) == json.loads(frame)
        ws.send_text(json.dumps({"type": "call_ended"}))
