"""Full-duplex call loop for the Dial audio socket.

Caller audio comes in on one side while spoken replies go out on the other,
paced at 20 ms per frame. Two things make it more than a send loop:

* **Barge-in.** If the caller starts talking while a reply is playing, the
  queued audio is dropped and a ``clear`` frame tells Dial to stop playback.
* **Decisions.** Caller speech is buffered until a pause; quiet-line decisions
  use ``decide_every_s``. The ``choose_reply`` callback is where the pipeline plugs
  in. The loop accepts approved phrase IDs or pre-rendered assistant questions.
* **Closing.** ``should_end`` lets the pipeline say the call is finished. The
  loop still waits for the reply queue to drain before hanging up, so the last
  thing the caller hears is a complete sentence rather than a cut-off one.

Receiving never waits on sending: the sender runs as its own task, so a long
reply cannot delay the caller's audio being read.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import struct
import wave
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .dial import FRAME_MS, DialSession, clear_frame, end_call_frame, reply_frames

log = logging.getLogger("dispatchiq.dial")

Receive = Callable[[], Awaitable[str]]
Send = Callable[[str], Awaitable[None]]
Synthesize = Callable[[str], bytes | None]  # approved phrase id -> WAV bytes
@dataclass(frozen=True)
class GeneratedReply:
    text: str
    language: str
    audio_wav: bytes = field(repr=False)


ChooseReply = Callable[[DialSession], Awaitable[str | GeneratedReply | None]]
ShouldEnd = Callable[[], bool]


def rms(pcm16: bytes) -> float:
    """Root-mean-square level of 16-bit little-endian PCM."""
    samples = [s for (s,) in struct.iter_unpack("<h", pcm16)]
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class CallLoop:
    def __init__(
        self,
        session: DialSession,
        *,
        send: Send,
        synthesize: Synthesize,
        choose_reply: ChooseReply | None = None,
        should_end: ShouldEnd | None = None,
        opening_phrase: str | None = None,
        barge_in_rms: float = 600.0,
        decide_every_s: float = 3.0,
        speech_pause_s: float = 0.9,
        speech_rms: float = 300.0,
        frame_interval: float = FRAME_MS / 1000,
    ) -> None:
        self.session = session
        self.synthesize = synthesize
        self.choose_reply = choose_reply
        self.should_end = should_end
        self.opening_phrase = opening_phrase
        self.barge_in_rms = barge_in_rms
        self.decide_every_s = decide_every_s
        self.speech_pause_s = speech_pause_s
        self.speech_rms = speech_rms
        self.frame_interval = frame_interval
        self.barge_ins = 0
        self._opened = False
        self._send_raw = send
        self._send_lock = asyncio.Lock()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._sending = False
        self._last_decision_s = 0.0
        self._speech_pending = False
        self._last_speech_s = 0.0

    @property
    def speaking(self) -> bool:
        return self._sending or not self._queue.empty()

    async def _send(self, frame: str) -> None:
        async with self._send_lock:
            await self._send_raw(frame)

    async def run(self, receive: Receive) -> None:
        sender = asyncio.create_task(self._sender())
        try:
            while not self.session.ended:
                raw = await receive()
                before = len(self.session.pcm16k)
                for outgoing in self.session.on_message(raw):
                    await self._send(outgoing)
                # The opening line goes out as soon as the call connects, so
                # the caller hears it before they speak. Dial sets the outbound
                # format on call_connected, so that is the earliest it is safe.
                if (
                    self.opening_phrase
                    and not self._opened
                    and self.session.outbound_format is not None
                    and not self.session.ended
                ):
                    self._opened = True
                    self._speak(self.opening_phrase)
                heard = bytes(self.session.pcm16k[before:])
                if heard and rms(heard) >= self.speech_rms:
                    self._speech_pending = True
                    self._last_speech_s = self.session.buffered_seconds()
                if heard and self.speaking and rms(heard) >= self.barge_in_rms:
                    await self._barge_in()
                await self._maybe_decide()
                if await self._maybe_hang_up():
                    break
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender
            # A disconnected socket may arrive without a call_ended frame.
            # File the last spoken words without trying to play a reply.
            self.session.ended = True
            await self._maybe_decide()

    async def _maybe_decide(self) -> None:
        if self.choose_reply is None or (self.speaking and not self.session.ended):
            return
        heard_s = self.session.buffered_seconds()
        if self.session.ended and not self._speech_pending:
            return
        if self._speech_pending:
            if not self.session.ended and heard_s - self._last_speech_s + 1e-6 < self.speech_pause_s:
                return
        elif heard_s - self._last_decision_s < self.decide_every_s:
            return
        self.session.utterance_ready = self._speech_pending
        self._speech_pending = False
        self._last_decision_s = heard_s
        try:
            phrase_id = await self.choose_reply(self.session)
        finally:
            self.session.utterance_ready = False
        if phrase_id is not None and not self.session.ended:
            self._speak(phrase_id)

    async def _maybe_hang_up(self) -> bool:
        """Close the call once the pipeline is done and the line is quiet.

        Waiting on ``speaking`` matters: hanging up the moment the closing
        phrase is queued would cut the caller off mid-reassurance, which is
        worse than not saying it at all.
        """
        if self.should_end is None or self.speaking or not self.should_end():
            return False
        log.info("call %s: pipeline finished, ending the call", self.session.call_id)
        self.session.ended = True
        await self._send(end_call_frame())
        return True

    def _speak(self, phrase_id: str | GeneratedReply) -> None:
        """Queue fixed or pre-rendered generated audio through the same pacing."""
        wav = phrase_id.audio_wav if isinstance(phrase_id, GeneratedReply) else self.synthesize(phrase_id)
        if wav is None:
            log.warning("no audio for %s on call %s", phrase_id, self.session.call_id)
            return
        codec = self.session.outbound_format or "mulaw_8000"
        try:
            frames = reply_frames(wav, codec)
        except (wave.Error, ValueError) as exc:
            # A bad reply must not take the call down. It is skipped, not spoken.
            log.warning("unusable reply audio for %s: %s", phrase_id, exc)
            return
        for frame in frames:
            self._queue.put_nowait(frame)

    async def _barge_in(self) -> None:
        dropped = 0
        while not self._queue.empty():
            self._queue.get_nowait()
            dropped += 1
        self.barge_ins += 1
        log.info("caller barged in on call %s, dropped %d frames", self.session.call_id, dropped)
        await self._send(clear_frame())

    async def _sender(self) -> None:
        while True:
            frame = await self._queue.get()
            self._sending = True
            try:
                await self._send(frame)
                await asyncio.sleep(self.frame_interval)
            finally:
                self._sending = False
