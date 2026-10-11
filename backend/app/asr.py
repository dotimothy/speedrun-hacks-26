"""Whisper transcription and translation, with the confidence it reports.

Two passes run over the same audio when the caller is not speaking English:
``transcribe`` gives the caller's own words, ``translate`` gives English. Both
are returned, because the dispatcher needs to see what was actually said and
the English pass is also what lets Laya be cross-checked against a second
rendering.

Whisper is the only component here that reports a confidence which genuinely
moves with input quality -- per-word probability, segment average logprob,
no-speech probability, and language probability (measured at 0.36 on a
non-speech alarm recording, against >0.99 on clear speech). ``confidence.py``
folds the per-word figure into every score, which is why a prediction drawn
from badly-heard audio scores lower than the same prediction from clean audio.

Model instances are cached because loading one costs seconds and the size is
switchable from the UI. The cache is capped: 24 GB will not hold every size
alongside the decision model.

On hardware
-----------
CTranslate2, which faster-whisper runs on, supports CUDA and CPU and **has no
Metal/MPS backend** -- ``get_supported_compute_types("mps")`` raises outright.
So on an Apple Silicon Mac this engine runs on the CPU with int8 quantisation,
which is the correct and only option, not a failure to find the GPU. Laya is
PyTorch and does use MPS there, so the decision stage stays fast; it is speech
recognition that pays, and it pays in proportion to clip length.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from .config import settings

log = logging.getLogger("dispatchiq.asr")


@dataclass
class Word:
    word: str
    start: float
    end: float
    probability: float


@dataclass
class Segment:
    text: str
    start: float
    end: float
    avg_logprob: float
    no_speech_prob: float
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcription:
    text: str
    language: str | None
    language_probability: float | None
    segments: list[Segment]
    duration: float = 0.0
    # Mean per-word probability across the clip. None on the transcript-input
    # path, where there is no audio and therefore no such thing -- never
    # defaulted to 1.0, because a fabricated confidence is worse than none.
    word_confidence: float | None = None


class WhisperEngine:
    """Lazily loaded, LRU-capped pool of Whisper models."""

    def __init__(self) -> None:
        self._models: OrderedDict[str, object] = OrderedDict()
        self._lock = threading.Lock()
        self._device: str | None = None
        self._compute_type: str | None = None
        self._load_error: str | None = None

    # ------------------------------------------------------------------
    # device selection
    # ------------------------------------------------------------------
    def _resolve_device(self) -> tuple[str, str]:
        """Pick the best device and compute type CTranslate2 actually offers.

        Asked rather than assumed, because the answer differs per machine:
        CUDA float16 on an NVIDIA box, CPU int8 on a Mac (CTranslate2 has no
        Metal backend), and CPU int8 wherever CUDA is present but unusable --
        a missing cuDNN being the usual cause on Windows. Falling back keeps
        the demo running where a crash would end it.
        """
        if self._device is not None and self._compute_type is not None:
            return self._device, self._compute_type

        try:
            import ctranslate2
        except Exception as exc:  # pragma: no cover - import guard
            log.warning("ctranslate2 unavailable (%s); assuming CPU int8", exc)
            self._device, self._compute_type = "cpu", "int8"
            return self._device, self._compute_type

        def supported(name: str) -> set[str]:
            try:
                return set(ctranslate2.get_supported_compute_types(name))
            except Exception:
                # Raises ValueError for a device this build has no backend
                # for -- "mps" always does. Not an error, just an answer.
                return set()

        for candidate in ("float16", "int8_float16", "bfloat16", "float32"):
            if candidate in supported("cuda"):
                self._device, self._compute_type = "cuda", candidate
                break
        else:
            cpu_types = supported("cpu")
            for candidate in ("int8", "int8_float32", "float32"):
                if candidate in cpu_types:
                    self._device, self._compute_type = "cpu", candidate
                    break
            else:  # pragma: no cover - no build ships without float32
                self._device, self._compute_type = "cpu", "float32"

            if self._is_apple_silicon():
                log.info(
                    "Apple Silicon: running Whisper on CPU %s. CTranslate2 has no "
                    "Metal backend, so this is the only option, not a fallback. "
                    "Laya still uses MPS.",
                    self._compute_type,
                )

        log.info("whisper device=%s compute_type=%s", self._device, self._compute_type)
        return self._device, self._compute_type

    @staticmethod
    def _is_apple_silicon() -> bool:
        import platform

        return platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64")

    @property
    def device(self) -> str:
        return self._resolve_device()[0]

    @property
    def compute_type(self) -> str:
        return self._resolve_device()[1]

    @property
    def load_error(self) -> str | None:
        return self._load_error

    # ------------------------------------------------------------------
    # model pool
    # ------------------------------------------------------------------
    def _get_model(self, size: str):
        with self._lock:
            if size in self._models:
                self._models.move_to_end(size)
                return self._models[size]

        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # pragma: no cover
            self._load_error = f"faster-whisper is not installed: {exc}"
            log.error(self._load_error)
            return None

        device, compute_type = self._resolve_device()
        try:
            model = WhisperModel(size, device=device, compute_type=compute_type)
        except Exception as exc:
            # The usual cause is a missing cuDNN; CPU int8 still works.
            log.warning("whisper %s failed on %s/%s (%s); falling back to CPU int8",
                        size, device, compute_type, exc)
            try:
                model = WhisperModel(size, device="cpu", compute_type="int8")
                self._device, self._compute_type = "cpu", "int8"
            except Exception as exc2:
                self._load_error = f"whisper {size} failed to load: {exc2}"
                log.error(self._load_error)
                return None

        with self._lock:
            self._models[size] = model
            self._models.move_to_end(size)
            while len(self._models) > settings.whisper_cache_max:
                evicted, old = self._models.popitem(last=False)
                log.info("evicting whisper model %s", evicted)
                del old
                self._free_vram()
        return model

    @staticmethod
    def _free_vram() -> None:
        """Release accelerator memory after evicting a model.

        Both backends are handled: an evicted model leaves cached blocks on
        whichever device was in use, and on a Mac the MPS allocator is the one
        holding them.
        """
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            mps = getattr(torch, "mps", None)
            if mps is not None and torch.backends.mps.is_available():
                mps.empty_cache()
        except Exception:  # pragma: no cover
            pass

    def loaded_sizes(self) -> list[str]:
        with self._lock:
            return list(self._models)

    def warmup(self, size: str | None = None) -> None:
        self._get_model(size or settings.whisper_default_size)

    # ------------------------------------------------------------------
    # inference
    # ------------------------------------------------------------------
    @staticmethod
    def _collect(raw_segments, info) -> Transcription:
        segments: list[Segment] = []
        all_probs: list[float] = []

        for seg in raw_segments:
            words = [
                Word(word=w.word, start=w.start, end=w.end, probability=w.probability)
                for w in (seg.words or [])
                if w.probability is not None
            ]
            all_probs.extend(w.probability for w in words)
            segments.append(
                Segment(
                    text=seg.text,
                    start=seg.start,
                    end=seg.end,
                    avg_logprob=seg.avg_logprob,
                    no_speech_prob=seg.no_speech_prob,
                    words=words,
                )
            )

        word_confidence = sum(all_probs) / len(all_probs) if all_probs else None
        if word_confidence is None and segments:
            # No word timestamps came back, so fall back to the segment
            # average logprob mapped through exp() -- a coarser but real signal.
            import math

            mean_logprob = sum(s.avg_logprob for s in segments) / len(segments)
            word_confidence = max(0.0, min(1.0, math.exp(mean_logprob)))

        return Transcription(
            text="".join(s.text for s in segments).strip(),
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            segments=segments,
            duration=float(getattr(info, "duration", 0.0) or 0.0),
            word_confidence=word_confidence,
        )

    def transcribe(self, audio_path: str, size: str, task: str = "transcribe",
                   language: str | None = None) -> Transcription | None:
        model = self._get_model(size)
        if model is None:
            return None
        raw, info = model.transcribe(
            audio_path,
            task=task,
            language=language,
            word_timestamps=True,
            # VAD matters for the abstention scenario: on a clip that is noise
            # rather than speech it returns no segments at all, which is the
            # honest answer and is what drives "await_more_audio".
            vad_filter=True,
            beam_size=5,
        )
        return self._collect(raw, info)

    def transcribe_and_translate(self, audio_path: str, size: str) -> tuple[Transcription | None, Transcription | None]:
        """Original-language pass, plus an English pass when it differs."""
        original = self.transcribe(audio_path, size, task="transcribe")
        if original is None:
            return None, None
        if (original.language or "en").startswith("en"):
            return original, original
        english = self.transcribe(audio_path, size, task="translate", language=original.language)
        return original, english


whisper_engine = WhisperEngine()
