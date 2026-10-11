"""Sample-format conversion for the audio transport layer.

Telephony delivers G.711 mu-law at 8 kHz. Whisper wants 16-bit PCM at 16 kHz.
Everything here is pure Python so the transport adds no dependencies, and it
is small enough to check by hand against the G.711 reference values.
"""

from __future__ import annotations

import base64
import io
import struct
import wave

BIAS = 0x84
CLIP = 32635
ASR_RATE = 16000


def mulaw_to_pcm16(data: bytes) -> bytes:
    """Decode G.711 mu-law bytes to 16-bit little-endian PCM."""
    out = bytearray(len(data) * 2)
    for i, byte in enumerate(data):
        u = ~byte & 0xFF
        t = (((u & 0x0F) << 3) + BIAS) << ((u & 0x70) >> 4)
        sample = (BIAS - t) if u & 0x80 else (t - BIAS)
        struct.pack_into("<h", out, i * 2, sample)
    return bytes(out)


def pcm16_to_mulaw(pcm: bytes) -> bytes:
    """Encode 16-bit little-endian PCM as G.711 mu-law bytes."""
    out = bytearray(len(pcm) // 2)
    for i, (sample,) in enumerate(struct.iter_unpack("<h", pcm)):
        sign = 0x80 if sample < 0 else 0
        magnitude = min(abs(sample), CLIP) + BIAS
        exponent = 7
        mask = 0x4000
        while exponent > 0 and not magnitude & mask:
            exponent -= 1
            mask >>= 1
        mantissa = (magnitude >> (exponent + 3)) & 0x0F
        out[i] = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return bytes(out)


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resample of mono 16-bit PCM.

    Linear interpolation is not a studio-quality resampler, but Whisper is
    tolerant of it and it keeps this module dependency-free. The 8 kHz to
    16 kHz case is exact at the original sample points.
    """
    if src_rate == dst_rate:
        return pcm
    samples = [s for (s,) in struct.iter_unpack("<h", pcm)]
    if not samples:
        return b""
    out_len = int(len(samples) * dst_rate / src_rate)
    out = bytearray(out_len * 2)
    last = len(samples) - 1
    for n in range(out_len):
        pos = n * src_rate / dst_rate
        lo = int(pos)
        hi = min(lo + 1, last)
        frac = pos - lo
        value = samples[lo] + (samples[hi] - samples[lo]) * frac
        struct.pack_into("<h", out, n * 2, max(-32768, min(32767, round(value))))
    return bytes(out)


def pcm16_to_wav(pcm: bytes, sample_rate: int = ASR_RATE) -> bytes:
    """Wrap mono 16-bit PCM in a WAV container for the ASR file interface."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def b64decode(payload: str) -> bytes:
    return base64.b64decode(payload, validate=True)
