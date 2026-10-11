"""Piper TTS over a fixed table of approved phrases.

The only way to get audio out of this module is to name one of the phrases
below. There is no code path that synthesises arbitrary text, which is
deliberate: a system that can speak to an emergency caller in a generated
sentence is a system that can say something nobody approved.

Note what the phrases are. Every one is a question the dispatcher would ask
anyway, or an instruction to stay on the line. None of them promises that help
is coming, states an arrival time, or implies that anything has been
dispatched -- the prototype has no authority to make those claims and the
phrase table is where that limit is enforced.

Phrases are rendered to WAV once at startup and cached, so pressing the button
during a demo plays an existing file rather than waiting on synthesis.
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path

from .config import VOICES_DIR, settings
from .schemas import ApprovedPhrase

log = logging.getLogger("dispatchiq.tts")

CACHE_DIR = VOICES_DIR / "_rendered"

# id -> (language, text). Bilingual because the product claim is multilingual;
# the Spanish lines are what the demo's non-English scenario needs.
APPROVED: dict[str, tuple[str, str]] = {
    "greeting_en": ("en", "Hello, what is your emergency?"),
    "stay_on_line_en": ("en", "Please stay on the line with me."),
    "what_happening_en": ("en", "Can you tell me what is happening right now?"),
    "repeat_address_en": ("en", "Can you repeat the street address for me?"),
    "ask_callback_en": ("en", "What number can we call you back on?"),
    "ask_injuries_en": ("en", "Is anyone hurt?"),
    "ask_safe_en": ("en", "Are you somewhere safe right now?"),
    "interpreter_en": ("en", "I am getting someone who speaks your language."),
    "stay_on_line_es": ("es", "Por favor, no cuelgue, quedese en la linea."),
    "what_happening_es": ("es", "Puede decirme que esta pasando ahora mismo?"),
    "repeat_address_es": ("es", "Puede repetir la direccion, por favor?"),
    "ask_callback_es": ("es", "A que numero podemos llamarle?"),
    "ask_injuries_es": ("es", "Hay alguien herido?"),
    "ask_safe_es": ("es", "Esta en un lugar seguro ahora?"),
    "interpreter_es": ("es", "Estoy buscando a alguien que hable su idioma."),
}

_voices: dict[str, object] = {}
_load_error: str | None = None


def approved_phrases() -> list[ApprovedPhrase]:
    return [
        ApprovedPhrase(id=pid, language=lang, text=text)
        for pid, (lang, text) in APPROVED.items()
    ]


def _voice_path(language: str) -> Path | None:
    name = settings.piper_voices.get(language)
    if not name:
        return None
    path = VOICES_DIR / f"{name}.onnx"
    return path if path.exists() else None


def _get_voice(language: str):
    global _load_error
    if language in _voices:
        return _voices[language]

    path = _voice_path(language)
    if path is None:
        _load_error = (
            f"no Piper voice for {language!r}; expected "
            f"{settings.piper_voices.get(language)}.onnx under {VOICES_DIR}"
        )
        return None

    try:
        from piper import PiperVoice

        _voices[language] = PiperVoice.load(str(path))
    except Exception as exc:
        _load_error = f"piper failed to load {path.name}: {exc}"
        log.warning(_load_error)
        return None
    return _voices[language]


def _render(phrase_id: str, language: str, text: str) -> Path | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # The speed is part of the cache key: changing it must produce new audio
    # rather than silently serving the previously rendered pace.
    speed = str(settings.piper_length_scale).replace(".", "")
    out = CACHE_DIR / f"{phrase_id}-s{speed}.wav"
    if out.exists():
        return out

    voice = _get_voice(language)
    if voice is None:
        return None
    try:
        import wave

        from piper import SynthesisConfig

        # Faster than Piper's default. See config.piper_length_scale for why.
        synthesis = SynthesisConfig(
            length_scale=settings.piper_length_scale,
            noise_scale=settings.piper_noise_scale,
            noise_w_scale=settings.piper_noise_w,
        )
        with wave.open(str(out), "wb") as handle:
            voice.synthesize_wav(text, handle, syn_config=synthesis)
    except Exception as exc:
        log.warning("piper synthesis failed for %s: %s", phrase_id, exc)
        out.unlink(missing_ok=True)
        return None
    return out


def prerender_all() -> dict[str, bool]:
    """Render every approved phrase up front. Safe to call repeatedly."""
    results: dict[str, bool] = {}
    for phrase_id, (language, text) in APPROVED.items():
        results[phrase_id] = _render(phrase_id, language, text) is not None
    return results


def speak(phrase_id: str) -> dict | None:
    """Return the WAV for an approved phrase, or the text if TTS is down.

    A failure here degrades to text rather than erroring: the documented
    fallback is that the dispatcher reads the line aloud, so the reply path
    stays usable with no voice model at all.
    """
    entry = APPROVED.get(phrase_id)
    if entry is None:
        return None
    language, text = entry

    started = time.perf_counter()
    path = _render(phrase_id, language, text)
    elapsed = round((time.perf_counter() - started) * 1000, 1)

    if path is None:
        return {
            "phrase_id": phrase_id,
            "language": language,
            "text": text,
            "audio_wav_base64": None,
            "spoken": False,
            "fallback": "Piper unavailable; read this line to the caller.",
            "error": _load_error,
            "latency_ms": elapsed,
        }

    return {
        "phrase_id": phrase_id,
        "language": language,
        "text": text,
        "audio_wav_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "spoken": True,
        "latency_ms": elapsed,
    }


def status() -> dict:
    rendered = sorted(p.stem for p in CACHE_DIR.glob("*.wav")) if CACHE_DIR.exists() else []
    _ = rendered
    return {
        "engine": "piper",
        "voices_configured": settings.piper_voices,
        "voices_present": {
            lang: (_voice_path(lang).name if _voice_path(lang) else None)
            for lang in settings.piper_voices
        },
        "phrases": len(APPROVED),
        "rendered": len(rendered),
        "length_scale": settings.piper_length_scale,
        "free_text_synthesis": "not available by design",
        "error": _load_error,
    }
