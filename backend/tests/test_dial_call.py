"""The duplex call loop: replies go out paced, and the caller can barge in.

Frames are scripted, and the sender runs with no pacing delay, so these tests
are deterministic and need no models or network.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import wave

from app.transport.audio import pcm16_to_mulaw
from app.transport.dial import DialSession, clear_frame
from app.transport.dial_call import CallLoop, rms
from app.transport.dial_call import GeneratedReply


def test_generated_audio_uses_the_paced_queue_and_supports_barge_in():
    async def scenario():
        sent = []
        async def send(frame): sent.append(frame)
        def fixed_speech(_): raise AssertionError("Generated audio is already rendered")
        loop = CallLoop(DialSession(call_id="generated"), send=send, synthesize=fixed_speech)
        loop._speak(GeneratedReply("Where are you?", "en", reply_wav(0.2)))
        assert not loop._queue.empty()
        assert json.loads(loop._queue.get_nowait())["type"] == "media"
        await loop._barge_in()
        assert loop._queue.empty()
        assert sent == [clear_frame()]
    asyncio.run(scenario())


def connected() -> str:
    return json.dumps(
        {
            "type": "call_connected",
            "audioInboundFormat": "mulaw_8000",
            "audioOutboundFormat": "mulaw_8000",
            "reconnect": False,
        }
    )


def caller_frame(level: int) -> str:
    """One 20 ms caller frame at a constant level."""
    pcm = level.to_bytes(2, "little", signed=True) * 160
    payload = base64.b64encode(pcm16_to_mulaw(pcm)).decode("ascii")
    return json.dumps({"type": "media", "payload": payload})


def reply_wav(seconds: float) -> bytes:
    buf = io.BytesIO()
    sample = (8000).to_bytes(2, "little", signed=True)
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(sample * int(seconds * 22050))
    return buf.getvalue()


def scripted(frames: list[str]):
    """A receive() that yields each frame, then a hang-up.

    Each call yields control once so the sender task can make progress.
    """
    queue = list(frames) + [json.dumps({"type": "call_ended"})]

    async def receive() -> str:
        await asyncio.sleep(0)
        return queue.pop(0)

    return receive


async def run_call(frames: list[str], *, reply: str | None = "stay_on_line_en",
                   wav: bytes | None = None, **kwargs) -> tuple[CallLoop, list[str]]:
    session = DialSession(call_id="call-1")
    sent: list[str] = []

    async def send(frame: str) -> None:
        sent.append(frame)

    async def choose(_session: DialSession) -> str | None:
        return reply

    def synthesize(_phrase_id: str) -> bytes | None:
        return reply_wav(1.0) if wav is None else wav

    loop = CallLoop(
        session,
        send=send,
        synthesize=synthesize,
        choose_reply=choose,
        frame_interval=0,
        **kwargs,
    )
    await loop.run(scripted([connected()] + frames))
    return loop, sent


def media_count(sent: list[str]) -> int:
    return sum(1 for f in sent if json.loads(f)["type"] == "media")


# 160 quiet frames is 3.2 s of caller audio, past the 3 s decision point.
QUIET = [caller_frame(0)] * 160


def test_reply_is_spoken_once_the_decision_point_passes() -> None:
    loop, sent = asyncio.run(run_call(QUIET + [caller_frame(0)] * 100))
    # One second of reply at 8 kHz mu-law is fifty 20 ms frames.
    assert media_count(sent) == 50
    assert clear_frame() not in sent


def test_loud_caller_audio_during_a_reply_barges_in() -> None:
    loud = caller_frame(20000)
    loop, sent = asyncio.run(run_call(QUIET + [loud] + [caller_frame(0)] * 5))
    assert clear_frame() in sent
    assert loop.barge_ins == 1
    # The reply was cut short, so fewer than the full fifty frames went out.
    assert media_count(sent) < 50


def test_quiet_caller_audio_does_not_barge_in() -> None:
    loop, sent = asyncio.run(run_call(QUIET + [caller_frame(0)] * 100))
    assert loop.barge_ins == 0
    assert clear_frame() not in sent


def test_loud_audio_with_nothing_playing_is_not_a_barge_in() -> None:
    # Speech before any reply has been queued has nothing to interrupt.
    loop, sent = asyncio.run(run_call([caller_frame(20000)] * 5, reply=None))
    assert loop.barge_ins == 0
    assert sent == []


def test_no_reply_is_sent_when_the_policy_declines() -> None:
    loop, sent = asyncio.run(run_call(QUIET + [caller_frame(0)] * 10, reply=None))
    assert media_count(sent) == 0


def test_unusable_reply_audio_is_skipped_not_fatal() -> None:
    loop, sent = asyncio.run(run_call(QUIET + [caller_frame(0)] * 10, wav=b"not a wav"))
    # A bad reply must not take the call down: nothing is spoken, and the
    # loop still reaches the hang-up.
    assert media_count(sent) == 0
    assert loop.session.ended


def test_opening_phrase_plays_once_on_connect() -> None:
    # The greeting goes out right after call_connected, before any caller audio.
    loop, sent = asyncio.run(
        run_call([caller_frame(0)] * 100, reply=None, opening_phrase="greeting_en")
    )
    assert media_count(sent) == 50
    assert json.loads(sent[0])["type"] == "media"
    assert loop.session.outbound_format == "mulaw_8000"


def test_call_ended_stops_the_loop_cleanly() -> None:
    loop, sent = asyncio.run(run_call(QUIET))
    assert loop.session.ended


def test_closing_audio_finishes_before_hangup() -> None:
    closing = False
    def should_end():
        return closing
    async def choose(_session):
        nonlocal closing
        closing = True
        return "wrap_up_en"
    async def scenario():
        sent = []
        async def send(frame): sent.append(frame)
        loop = CallLoop(DialSession(call_id="closing"), send=send,
                        synthesize=lambda _: reply_wav(1.0), choose_reply=choose,
                        should_end=should_end, frame_interval=0)
        await loop.run(scripted([connected()] + QUIET + [caller_frame(0)] * 100))
        return loop, sent
    loop, sent = asyncio.run(scenario())
    assert closing and loop.session.ended
    assert media_count(sent) == 50
    assert json.loads(sent[-1])["type"] == "end_call"
    assert all(json.loads(frame)["type"] == "media" for frame in sent[:-1])


def test_rms_reads_level() -> None:
    silence = (0).to_bytes(2, "little", signed=True) * 100
    loud = (1000).to_bytes(2, "little", signed=True) * 100
    assert rms(silence) == 0
    assert rms(loud) == 1000
