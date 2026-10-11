"""The Dial transport's invariants: signatures, codecs and frame handling.

No network and no Dial account are needed. The socket test drives the real
route with Starlette's test client, using a secret we choose.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
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
def pipeline_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bytes | None]]:
    """Stand in for the real models: record what the socket hands to the pipeline."""
    calls: list[tuple[str, bytes | None]] = []

    async def fake_pipeline(req, audio_bytes):  # type: ignore[no-untyped-def]
        calls.append((req.session_id, audio_bytes))
        return main.ProcessResponse.model_construct(session_id=req.session_id)

    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(main, "DIAL_RESULTS", {})
    return calls


@pytest.fixture
def dial_enabled(monkeypatch: pytest.MonkeyPatch, pipeline_calls: list) -> None:
    monkeypatch.setattr(settings, "dial_signing_secret", SECRET)
    # Keep Piper out of the socket tests: the greeting gets a short stub WAV.
    monkeypatch.setattr(main, "_dial_reply_audio", lambda _phrase_id: synth_wav(0.1, 22050))


def test_socket_refuses_when_no_secret_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dial_signing_secret", "")
    client = TestClient(main.app)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/dispatchiq/audio/{CALL}", headers={"x-dial-signature": sign()}
    ):
        pass


def test_socket_refuses_bad_signature(dial_enabled: None) -> None:
    client = TestClient(main.app)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/dispatchiq/audio/{CALL}", headers={"x-dial-signature": sign(secret="wrong")}
    ):
        pass


def test_socket_opens_with_the_greeting_once_connected(dial_enabled: None) -> None:
    client = TestClient(main.app)
    with client.websocket_connect(
        f"/dispatchiq/audio/{CALL}", headers={"x-dial-signature": sign()}
    ) as ws:
        ws.send_text(connected())
        # The first thing the caller hears is the greeting, as media frames.
        assert json.loads(ws.receive_text())["type"] == "media"
        ws.send_text(json.dumps({"type": "call_ended"}))


def test_finished_call_is_sent_through_the_pipeline_as_16k_wav(
    dial_enabled: None, pipeline_calls: list
) -> None:
    import io
    import wave

    client = TestClient(main.app)
    with client.websocket_connect(
        f"/dispatchiq/audio/{CALL}", headers={"x-dial-signature": sign()}
    ) as ws:
        ws.send_text(connected())
        ws.send_text(media(b"\x7f" * 160))
        ws.receive_text()
        ws.send_text(json.dumps({"type": "call_ended"}))

    # The socket closes after the pipeline has run, so the call is recorded.
    assert [sid for sid, _ in pipeline_calls] == [CALL]
    wav = pipeline_calls[0][1]
    with wave.open(io.BytesIO(wav)) as f:
        assert f.getframerate() == 16000
        assert f.getnframes() == 320  # 20 ms of 8 kHz audio, resampled

    result = client.get(f"/api/dispatchiq/calls/{CALL}")
    assert result.status_code == 200
    assert result.json()["session_id"] == CALL


def test_result_endpoint_is_404_until_a_call_is_analysed(pipeline_calls: list) -> None:
    client = TestClient(main.app)
    assert client.get("/api/dispatchiq/calls/never-happened").status_code == 404


# --- outbound reply audio --------------------------------------------------


def synth_wav(seconds: float, rate: int, level: int = 8000, channels: int = 1) -> bytes:
    """A constant-level WAV, standing in for a Piper reply."""
    import io
    import wave

    buf = io.BytesIO()
    frames = int(seconds * rate)
    sample = level.to_bytes(2, "little", signed=True) * channels
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(sample * frames)
    return buf.getvalue()


def decode_frames(frames: list[str]) -> bytes:
    from app.transport.audio import mulaw_to_pcm16

    raw = b"".join(base64.b64decode(json.loads(f)["payload"]) for f in frames)
    return mulaw_to_pcm16(raw)


def test_reply_frames_are_20ms_mulaw_at_8k() -> None:
    from app.transport.dial import reply_frames

    frames = reply_frames(synth_wav(seconds=1.0, rate=22050))
    # One second is fifty 20 ms frames of 160 mu-law bytes each.
    assert len(frames) == 50
    for frame in frames:
        assert len(base64.b64decode(json.loads(frame)["payload"])) == 160
        assert json.loads(frame)["type"] == "media"


def test_reply_audio_keeps_its_level_after_encoding() -> None:
    from app.transport.dial import reply_frames

    decoded = decode_frames(reply_frames(synth_wav(seconds=0.5, rate=22050, level=8000)))
    samples = [s for (s,) in struct.iter_unpack("<h", decoded)]
    # mu-law is lossy, so allow a few percent of error on a constant level.
    assert abs(sum(samples) / len(samples) - 8000) < 8000 * 0.05


def test_stereo_reply_uses_its_left_channel() -> None:
    from app.transport.audio import wav_to_pcm16

    pcm, rate = wav_to_pcm16(synth_wav(seconds=0.1, rate=16000, level=1234, channels=2))
    assert rate == 16000
    assert {s for (s,) in struct.iter_unpack("<h", pcm)} == {1234}


def test_reply_can_be_sent_as_linear_pcm() -> None:
    from app.transport.dial import reply_frames

    frames = reply_frames(synth_wav(seconds=1.0, rate=22050), outbound_format="l16_16000")
    # One second at 16 kHz, 16-bit: 32000 bytes in 640-byte frames.
    assert len(frames) == 50
    assert len(base64.b64decode(json.loads(frames[0])["payload"])) == 640


def test_clear_frame_is_type_only() -> None:
    from app.transport.dial import clear_frame

    assert json.loads(clear_frame()) == {"type": "clear"}
