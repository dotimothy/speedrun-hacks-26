"""Runtime configuration. Everything here is overridable by environment var."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures"
VOICES_DIR = REPO_ROOT / "voices"


def _load_dotenv() -> None:
    """Read .env into the environment if it exists.

    Hand-rolled rather than pulling in python-dotenv: it is fifteen lines, and
    the point is that a key lives in a gitignored file instead of in the
    source, which does not need a dependency. Existing environment variables
    win, so a real deployment's configuration is never overridden by a file
    somebody left behind.
    """
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


_load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    # --- ASR ---------------------------------------------------------------
    whisper_default_size: str = _env("DISPATCHIQ_WHISPER_SIZE", "small")
    # Sizes offered in the UI picker. Larger ones download on first use.
    whisper_sizes: tuple[str, ...] = ("tiny", "base", "small", "medium", "large-v3")
    # At most this many Whisper models stay resident; the rest are LRU-evicted.
    whisper_cache_max: int = int(_env("DISPATCHIQ_WHISPER_CACHE", "2"))

    # --- Laya (primary decision engine) ------------------------------------
    # Typed decisions in one non-autoregressive forward pass. Measured on this
    # box: 35-45 ms warm for the full question set, against 2.3-10.5 s for a
    # generative model asked the same thing.
    laya_default_checkpoint: str = _env("DISPATCHIQ_LAYA_MODEL", "multilingual")
    laya_device: str = _env("DISPATCHIQ_LAYA_DEVICE", "")  # "" -> laya autodetects
    laya_max_loaded: int = int(_env("DISPATCHIQ_LAYA_MAX_LOADED", "2"))
    laya_preload: bool = _env("DISPATCHIQ_LAYA_PRELOAD", "1") == "1"

    # Abstention thresholds. Below these the value is reported as ``unknown``
    # rather than guessed -- this is where most ``unknown`` values come from.
    # They are deliberately conservative because the checkpoints are
    # over-confident (laya issue #394); they are NOT fitted thresholds and we
    # do not present them as calibrated.
    choice_min_confidence: float = float(_env("DISPATCHIQ_MIN_CHOICE", "0.55"))
    noul_high: float = float(_env("DISPATCHIQ_NOUL_HIGH", "0.70"))
    noul_low: float = float(_env("DISPATCHIQ_NOUL_LOW", "0.30"))
    # Penalty applied when the original-language pass and the English pass
    # disagree. Measured: a Spanish kitchen-fire call routed to the
    # multilingual checkpoint returned gas_leak at 0.956 while the English
    # translation returned structure_fire at 0.867. Disagreement is signal.
    conflict_penalty: float = float(_env("DISPATCHIQ_CONFLICT_PENALTY", "0.5"))
    # A field whose value has no locatable support in the transcript AND no
    # agreement from an independent checker is capped here -- below the
    # abstention threshold, so it is reported as unknown. Measured need: on a
    # near-empty call ("something is wrong, I can't really tell") the model
    # returned injuries=suspected at 0.785, which no word in the transcript
    # supports. A product that claims its fields are evidence-backed cannot
    # show that number.
    uncorroborated_cap: float = float(_env("DISPATCHIQ_UNCORROBORATED_CAP", "0.35"))

    CALIBRATION_WARNING: str = (
        "Scores are raw model probabilities, not calibrated. Laya ships "
        "temperatures its own docs flag as invalid for these option counts "
        "(issue #394), so a 0.9 here does not mean 90% correct. Treat every "
        "field as needing review."
    )

    # --- Decision engine selection -----------------------------------------
    # Which engine answers the typed questions. The swap point is the
    # DecisionEngine protocol; see decision/registry.py.
    decision_engine: str = _env("DISPATCHIQ_DECISION_ENGINE", "laya")

    # Cloud inference is off unless switched on here, and cannot be enabled by
    # a request parameter. Every other engine runs on this machine, and the
    # project's pitch says so; an engine that sends a caller's words to a
    # third party changes that claim, so turning it on should be a deliberate
    # act recorded in configuration rather than a query string.
    allow_cloud_inference: bool = _env("DISPATCHIQ_ALLOW_CLOUD", "0") == "1"
    openai_api_key: str = _env("OPENAI_API_KEY", "")
    openai_model: str = _env("DISPATCHIQ_OPENAI_MODEL", "gpt-4o-mini")
    decisions_model: str = _env("DISPATCHIQ_DECISIONS_MODEL", "gpt-6-luna")
    openai_timeout_s: float = float(_env("DISPATCHIQ_OPENAI_TIMEOUT", "20"))
    assistant_enabled: bool = _env("DISPATCHIQ_ASSISTANT_ENABLED", "1") == "1"
    assistant_model: str = _env("DISPATCHIQ_ASSISTANT_MODEL", "gpt-6-luna")
    assistant_timeout_s: float = float(_env("DISPATCHIQ_ASSISTANT_TIMEOUT", "15"))
    # What a typed answer is worth before anything corroborates it, for
    # engines that report no probability of their own. Deliberately middling:
    # one model asserting something, with nothing agreeing with it yet, is
    # neither strong nor worthless. Agreement and evidence move it from here.
    typed_answer_prior: float = float(_env("DISPATCHIQ_TYPED_PRIOR", "0.6"))

    # Jev / TypeSafe System-One. `jev_is_local` exists because locality here
    # depends on where the endpoint is: a self-hosted deployment runs on your
    # hardware, the hosted service does not, and the health report should not
    # guess.
    jev_endpoint: str = _env("DISPATCHIQ_JEV_ENDPOINT", "")
    jev_api_key: str = _env("DISPATCHIQ_JEV_API_KEY", "")
    jev_model: str = _env("DISPATCHIQ_JEV_MODEL", "")
    jev_is_local: bool = _env("DISPATCHIQ_JEV_IS_LOCAL", "0") == "1"

    # --- Generative extractor (optional, secondary) ------------------------
    ollama_host: str = _env("OLLAMA_HOST", "http://127.0.0.1:11434")
    # Measured warm latency on this box: gemma3:latest is the fastest of the
    # locally available models; bench_latency.py re-checks and docs/LATENCY.md
    # records the result.
    decision_model: str = _env("DISPATCHIQ_DECISION_MODEL", "gemma3:latest")
    # Keep the model resident so we pay the ~80s load once per session.
    ollama_keep_alive: str = _env("DISPATCHIQ_KEEP_ALIVE", "30m")
    # Cap generation: the schema output is small, and an uncapped model was
    # measured wandering from 2.3s to 10.5s on identical input.
    num_predict: int = int(_env("DISPATCHIQ_NUM_PREDICT", "320"))
    decision_timeout_s: float = float(_env("DISPATCHIQ_DECISION_TIMEOUT", "90"))

    # --- Confidence --------------------------------------------------------
    # Derived-score ladder. See confidence.py for why self-reported scores are
    # not used at all.
    score_agreement: float = 0.9
    score_llm_only: float = 0.65
    score_conflict: float = 0.4
    score_abstain: float = 0.0
    fuzzy_threshold: float = 0.9

    # --- TTS ---------------------------------------------------------------
    # Piper's length_scale is seconds-per-phoneme: lower is faster. The
    # default of 1.0 reads at an audiobook pace, which is wrong for this --
    # a dispatcher asking "what is the address?" says it briskly, and in a
    # demo every extra second of synthetic speech is a second nobody is
    # watching the form fill in. 0.7 is noticeably quicker and still clear.
    piper_length_scale: float = float(_env("DISPATCHIQ_TTS_SPEED", "0.7"))
    # Slightly tighter than default prosody, which suits short questions.
    piper_noise_scale: float = float(_env("DISPATCHIQ_TTS_NOISE", "0.55"))
    piper_noise_w: float = float(_env("DISPATCHIQ_TTS_NOISE_W", "0.7"))
    piper_voices: dict[str, str] = field(
        default_factory=lambda: {
            "en": _env("DISPATCHIQ_VOICE_EN", "en_US-amy-medium"),
            "es": _env("DISPATCHIQ_VOICE_ES", "es_MX-ald-medium"),
        }
    )

    # --- Server ------------------------------------------------------------
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://localhost:5173",
        "https://127.0.0.1:5173",
    )
    # The dashboard may also be opened on a phone at the machine's LAN
    # address, which cannot be known ahead of time. This allows private-range
    # origins only -- the prototype is meant to run on a local network and
    # nothing here should be exposed to the internet.
    cors_origin_regex: str = _env(
        "DISPATCHIQ_CORS_REGEX",
        r"https?://(localhost|127\.0\.0\.1|\[::1\]|10\.\d+\.\d+\.\d+"
        r"|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?",
    )

    # --- TLS ---------------------------------------------------------------
    # Populated by scripts/make_cert.py. HTTPS matters because getUserMedia
    # refuses to run in an insecure context, and a LAN address is one.
    ssl_certfile: str = _env("DISPATCHIQ_SSL_CERT", "")
    ssl_keyfile: str = _env("DISPATCHIQ_SSL_KEY", "")
    # Serve committed fixture responses instead of running models. Lets the
    # frontend be built before the backend is live, and is the documented
    # fallback if the GPU path fails during the demo.
    mock_mode: bool = _env("DISPATCHIQ_MOCK", "0") == "1"

    # --- Dial audio transport (optional) -----------------------------------
    # Signing secret from GET /api/v1/self-hosted/secret?mode=audio. The
    # /dispatchiq/audio/{call_id} socket refuses every connection while this is empty.
    dial_signing_secret: str = _env("DIAL_SIGNING_SECRET", "")
    # Frames older than this are rejected, which stops a captured handshake
    # from being replayed later.
    dial_signature_tolerance_s: int = int(_env("DIAL_SIGNATURE_TOLERANCE", "300"))
    # LLM mode has its own secret, from GET /api/v1/self-hosted/secret?mode=llm.
    # The /dispatchiq/llm/{call_id} socket refuses connections while it is empty.
    dial_llm_signing_secret: str = _env("DIAL_LLM_SIGNING_SECRET", "")

    # How the call itself is paced. These were constants in three different
    # files, which is awkward mid-demo: how often the agent may speak and how
    # much audio a transcription pass gets are exactly the knobs you want to
    # turn while listening to it, not after editing code and restarting.
    dial_opening_phrase: str = _env("DIAL_OPENING_PHRASE", "greeting_en")
    # Seconds of caller audio between decision ticks. Each tick transcribes,
    # files a segment, and may speak. Lower feels more responsive and gives
    # Whisper less to work with.
    dial_decide_every_s: float = float(_env("DIAL_DECIDE_EVERY_S", "3.0"))
    # Audio shorter than this is not sent to Whisper: a breath or a syllable
    # is where hallucinated filler comes from.
    dial_min_chunk_s: float = float(_env("DIAL_MIN_CHUNK_S", "1.8"))
    # Whisper size for live call chunks, separate from the dashboard default.
    # A phone call is 8 kHz and chunked, so a bigger model buys less here than
    # it does on a clean recording, while costing latency the caller feels.
    dial_whisper_size: str = _env("DIAL_WHISPER_SIZE", "small")

    # Numbers permitted to reach the demo. Dial has no allowlist of its own,
    # so this is the only thing standing between a live public number and
    # whoever dials it. Empty means anyone, which is fine for a local
    # prototype and wrong for a number on the telephone network.
    allowed_callers: tuple[str, ...] = tuple(
        n.strip() for n in _env("DISPATCHIQ_ALLOWED_CALLERS", "").split(",") if n.strip()
    )


settings = Settings()
