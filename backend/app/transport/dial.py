"""Dial Self-Hosted audio transport.

Dial opens one WebSocket per call to our server, at ``/dial/{call_id}``. Each
connection carries an ``X-Dial-Signature: t=<unix>,v1=<hex>`` header, where the
hex digest is HMAC-SHA256(secret, "<t>.<call_id>").

This module is the transport layer only. Per plan.md, Dial replaces the
browser audio transport and nothing else: Whisper, Laya and the workflow engine
see the same 16 kHz PCM either way. Nothing here reaches the network, so the
session can be exercised with recorded frames before Dial access is granted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field

from .audio import b64decode, mulaw_to_pcm16, resample_pcm16

log = logging.getLogger("dispatchiq.dial")

# Formats we can decode into 16 kHz PCM for ASR. Anything else is refused at
# call_connected rather than guessed at.
SUPPORTED_FORMATS: dict[str, tuple[str, int]] = {
    "mulaw_8000": ("mulaw", 8000),
    "l16_8000": ("pcm", 8000),
    "l16_16000": ("pcm", 16000),
}

# ASSUMPTION: the audio frames carry their base64 body in a ``payload`` field.
# plan.md only says "base64 in media frames"; confirm the exact field name
# against Dial's audio protocol before live testing and change it here.
PAYLOAD_FIELD = "payload"


def parse_signature(header: str | None) -> tuple[int, str] | None:
    """Split ``t=<unix>,v1=<hex>`` into its timestamp and digest."""
    if not header:
        return None
    parts = dict(
        item.split("=", 1) for item in header.split(",") if "=" in item
    )
    try:
        return int(parts["t"]), parts["v1"]
    except (KeyError, ValueError):
        return None


def verify_signature(
    header: str | None,
    call_id: str,
    secret: str,
    *,
    tolerance_s: int = 300,
    now: float | None = None,
) -> bool:
    """True only for a fresh, correctly signed header bound to this call."""
    if not secret:
        return False
    parsed = parse_signature(header)
    if parsed is None:
        return False
    timestamp, digest = parsed
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_s:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{call_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    # Constant-time compare so the digest cannot be recovered byte by byte.
    return hmac.compare_digest(expected, digest)


@dataclass
class DialSession:
    """Per-call state machine for one Dial audio socket.

    ``on_message`` takes one raw text frame and returns the frames to send back
    immediately. Caller audio accumulates in ``pcm16k`` for the ASR path.
    """

    call_id: str
    echo: bool = False
    inbound_format: str | None = None
    outbound_format: str | None = None
    reconnect: bool = False
    ended: bool = False
    pcm16k: bytearray = field(default_factory=bytearray)
    dtmf_digits: list[str] = field(default_factory=list)

    def on_message(self, raw: str) -> list[str]:
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("dropping non-JSON frame on call %s", self.call_id)
            return []

        kind = frame.get("type")
        if kind == "call_connected":
            return self._connected(frame)
        if kind == "media":
            return self._media(raw, frame)
        if kind == "dtmf":
            self.dtmf_digits.append(str(frame.get("digit", "")))
            return []
        if kind == "call_ended":
            self.ended = True
            return []
        if kind == "ping_pong":
            # Dial expects keepalives echoed back unchanged.
            return [raw]
        # mark, duration_warning and anything new are not acted on yet.
        return []

    def _connected(self, frame: dict) -> list[str]:
        self.inbound_format = frame.get("audioInboundFormat", "mulaw_8000")
        self.outbound_format = frame.get("audioOutboundFormat", "mulaw_8000")
        self.reconnect = bool(frame.get("reconnect", False))
        for name in (self.inbound_format, self.outbound_format):
            if name not in SUPPORTED_FORMATS:
                log.error("call %s uses unsupported format %s", self.call_id, name)
                self.ended = True
                return [json.dumps({"type": "end_call"})]
        return []

    def _media(self, raw: str, frame: dict) -> list[str]:
        if self.inbound_format is None:
            # Media before call_connected cannot be decoded; drop it.
            return []
        encoding, rate = SUPPORTED_FORMATS[self.inbound_format]
        try:
            data = b64decode(frame[PAYLOAD_FIELD])
        except (KeyError, ValueError, TypeError):
            log.warning("bad media frame on call %s", self.call_id)
            return []

        pcm = mulaw_to_pcm16(data) if encoding == "mulaw" else data
        self.pcm16k += resample_pcm16(pcm, rate, 16000)

        # Echo is only exact when both directions use the same format, because
        # the inbound payload can then go straight back out untouched.
        if self.echo and self.inbound_format == self.outbound_format:
            return [json.dumps({"type": "media", PAYLOAD_FIELD: frame[PAYLOAD_FIELD]})]
        return []

    def buffered_seconds(self) -> float:
        return len(self.pcm16k) / 2 / 16000
