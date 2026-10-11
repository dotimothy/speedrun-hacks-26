"""DispatchIQ HTTP API.

One endpoint does the work. ``POST /api/process`` takes either a WAV upload or
a plain transcript and returns the structured, reviewable result. Everything
else is support: health, the model pickers the UI offers, and the
approved-phrase reply path.

The pipeline, in order:

    audio -> Whisper (original + English, with per-word confidence)
          -> Laya   (typed answers, both languages, with probabilities)
          -> rules  (deterministic patterns, as the second opinion)
          -> confidence policy (derive scores, abstain to unknown)
          -> workflow engine   (deterministic action, protect confirmed fields)
"""

from __future__ import annotations

import base64
import json
import logging
import platform
import os
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import tts
from .asr import whisper_engine
from .live.dial_bridge import bridge_for, close_bridge
from .live.router import router as live_router
from .autoreply import decide as decide_auto_reply
from .config import FIXTURES_DIR, settings
from .confidence import resolve as resolve_confidence
from .decision.laya_model import LayaEngine
from .decision.registry import describe_all as describe_engines
from .decision.rules import RuleEngine
from .schemas import (
    AutoReplySuggestion,
    LatencyBreakdown,
    ProcessRequest,
    ProcessResponse,
    SpeakRequest,
)
from .timing import StageTimer
from .transport.audio import pcm16_to_wav
from .transport.dial import DialSession, verify_signature
from .transport.dial_call import CallLoop
from .transport.dial_llm import LlmSession, handle_frame
from .workflow import apply as apply_workflow

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dispatchiq")

laya_engine = LayaEngine()
rule_engine = RuleEngine()

# Per-session memory of what a human has confirmed. In-process on purpose: a
# prototype that keeps dispatcher corrections in a database it cannot explain
# is worse than one that forgets them when it restarts.
SESSIONS: dict[str, dict[str, object]] = {}

# Phrases autonomous mode has already spoken, per session. Kept server-side so
# the no-repeat rule survives a page reload and cannot be reset by a client
# that simply forgets.
SPOKEN: dict[str, list[str]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm both models up front. Laya's first call pays a checkpoint download
    # and Whisper's pays a model load; doing it here keeps the measured latency
    # in the demo the warm steady-state figure rather than a one-off cold start.
    if not settings.mock_mode:
        try:
            laya_engine.warmup()
        except Exception as exc:  # pragma: no cover
            log.warning("laya warmup failed: %s", exc)
        try:
            whisper_engine.warmup()
        except Exception as exc:  # pragma: no cover
            log.warning("whisper warmup failed: %s", exc)
    yield


app = FastAPI(title="DispatchIQ", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_origin_regex=settings.cors_origin_regex,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _fixture_response(name: str) -> dict | None:
    path = FIXTURES_DIR / "scenarios" / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


async def _parse_request(request: Request) -> tuple[ProcessRequest, bytes | None]:
    """Accept JSON for the transcript path, multipart for the audio path.

    The contract specifies one endpoint taking either input, so the branch
    happens here rather than splitting the API in two.
    """
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw = form.get("payload")
        payload = json.loads(raw) if isinstance(raw, str) else {}
        upload = form.get("audio")
        audio_bytes = await upload.read() if hasattr(upload, "read") else None
        return ProcessRequest(**payload), audio_bytes

    body = await request.json()
    return ProcessRequest(**body), None


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
# Live sessions (API v2, issue #1) live in their own router so that mounting
# them is a one-line change here -- a teammate's Dial adapter is editing this
# same file on another branch.
app.include_router(live_router)


# The dashboard plays the committed fixture audio through the real pipeline,
# so the WAVs and the recorded expectations are served from here. Synthetic
# material only -- there is no real call audio in this repository.
if FIXTURES_DIR.exists():
    app.mount("/fixtures", StaticFiles(directory=str(FIXTURES_DIR)), name="fixtures")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "mock_mode": settings.mock_mode,
        "decision_default": settings.decision_engine,
        "whisper": {
            "device": whisper_engine.device,
            "compute_type": whisper_engine.compute_type,
            "loaded": whisper_engine.loaded_sizes(),
            "default_size": settings.whisper_default_size,
            "error": whisper_engine.load_error,
        },
        "laya": {
            "ready": laya_engine.ready(),
            "checkpoint": settings.laya_default_checkpoint,
            "device": laya_engine.device,
            "error": laya_engine.load_error,
        },
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "decision_engines": describe_engines(),
        "inference_is_local": all(
            e.get("is_local", True) for e in describe_engines() if e.get("ready")
        ),
        "tts": tts.status(),
        "calibration_warning": settings.CALIBRATION_WARNING,
    }


@app.get("/api/models")
def models() -> dict:
    """What the UI is allowed to offer in its pickers."""
    from .live.assistant import availability
    return {
        "whisper_sizes": list(settings.whisper_sizes),
        "whisper_default": settings.whisper_default_size,
        "whisper_loaded": whisper_engine.loaded_sizes(),
        "decision_engines": describe_engines(),
        "decision_default": settings.decision_engine,
        "call_assistant": availability(),
        "cloud_inference_enabled": settings.allow_cloud_inference,
        "laya_checkpoints": ["multilingual", "english", "typed-decisions"],
        "thresholds": {
            "choice_min_confidence": settings.choice_min_confidence,
            "noul_high": settings.noul_high,
            "noul_low": settings.noul_low,
        },
    }


@app.get("/api/phrases")
def phrases() -> dict:
    return {"phrases": [p.model_dump() for p in tts.approved_phrases()]}


@app.post("/api/speak")
def speak(req: SpeakRequest) -> dict:
    """Synthesise one pre-approved phrase. Free text is not reachable here."""
    result = tts.speak(req.phrase_id)
    if result is None:
        raise HTTPException(status_code=404, detail="unknown phrase id")
    if req.session_id:
        # Recorded whether a human pressed the button or autonomous mode did,
        # so the no-repeat rule covers both and the session has one audit list.
        spoken = SPOKEN.setdefault(req.session_id, [])
        spoken.append(req.phrase_id)
        result["spoken_this_session"] = list(spoken)
    return result


@app.get("/api/sessions/{session_id}/spoken")
def spoken(session_id: str) -> dict:
    """Everything that has been said aloud to this caller, in order."""
    ids = SPOKEN.get(session_id, [])
    return {
        "session_id": session_id,
        "phrases": [
            {"id": pid, "language": tts.APPROVED[pid][0], "text": tts.APPROVED[pid][1]}
            for pid in ids
            if pid in tts.APPROVED
        ],
    }


@app.post("/api/process", response_model=ProcessResponse)
async def process(request: Request) -> ProcessResponse:
    req, audio_bytes = await _parse_request(request)

    if settings.mock_mode:
        fixture = _fixture_response(req.session_id)
        if fixture is not None:
            return ProcessResponse(**fixture)

    return await _run_pipeline(req, audio_bytes)


async def _run_pipeline(req: ProcessRequest, audio_bytes: bytes | None) -> ProcessResponse:
    """ASR, decisions, workflow and reply for one request.

    Shared by the HTTP endpoint and the Dial socket, so a call's audio goes
    through exactly the same path as an uploaded WAV.
    """
    timer = StageTimer()

    if not audio_bytes and not (req.transcript or "").strip():
        raise HTTPException(status_code=400, detail="provide either audio or transcript")

    # ---------------------------------------------------------------- ASR
    original = (req.transcript or "").strip()
    english = original
    language: str | None = None
    language_probability: float | None = None
    word_confidence: float | None = None
    has_speech = bool(original)
    notes: list[str] = []

    if audio_bytes:
        # faster-whisper reads from a path, so the upload is spooled to one.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(audio_bytes)
            audio_path = handle.name
        try:
            with timer.stage("asr"):
                orig_t, eng_t = whisper_engine.transcribe_and_translate(
                    audio_path, req.whisper_size
                )
            if orig_t is None:
                raise HTTPException(status_code=503, detail=whisper_engine.load_error or "ASR failed")
            original = orig_t.text
            english = (eng_t.text if eng_t else orig_t.text) or orig_t.text
            language = orig_t.language
            language_probability = orig_t.language_probability
            word_confidence = orig_t.word_confidence
            # VAD returning nothing is a real answer, not an error.
            has_speech = bool(orig_t.segments) and bool(original.strip())
            if not has_speech:
                notes.append(
                    "Voice activity detection found no speech in this audio. "
                    "No fields were inferred."
                )
        finally:
            os.unlink(audio_path)
    else:
        notes.append(
            "Transcript input: there is no audio, so no ASR confidence exists "
            "and none was assumed."
        )

    # ----------------------------------------------------------- decisions
    laya_result = None
    if req.decision_engine == "laya" and has_speech:
        with timer.stage("decision"):
            laya_result = laya_engine.extract(original, english, language)
        notes.extend(laya_result.notes)

    with timer.stage("rules"):
        rule_result = rule_engine.extract(original, english, language)

    resolution = resolve_confidence(
        laya_result, rule_result, original, english, asr_confidence=word_confidence
    )
    notes.extend(resolution.notes)

    # ------------------------------------------------------------ workflow
    confirmed = dict(SESSIONS.get(req.session_id, {}))
    confirmed.update(req.confirmed or {})
    if confirmed:
        SESSIONS[req.session_id] = confirmed

    outcome = apply_workflow(
        resolution.predictions,
        resolution.form,
        confirmed=confirmed,
        has_speech=has_speech,
        language_probability=language_probability,
    )
    notes.extend(outcome.notes)

    latency = LatencyBreakdown(
        asr=timer.get("asr"),
        translate=0.0,  # measured inside the ASR stage; both passes are one call
        decision=timer.get("decision") + timer.get("rules"),
        tts=0.0,
        server_total=timer.server_total_ms,
        # The server cannot see the network. The client fills these in and
        # labels them client-measured rather than letting us invent a number.
        network=None,
        end_to_end=None,
    )

    reply, quiet_reason = decide_auto_reply(
        suggested_action=outcome.suggested_action.value,
        predictions=outcome.predictions,
        detected_language=language,
        confirmed=confirmed,
        already_asked=SPOKEN.get(req.session_id, []),
    )
    if reply is not None:
        phrase = tts.APPROVED.get(reply.phrase_id)
        auto_reply = AutoReplySuggestion(
            phrase_id=reply.phrase_id,
            language=reply.language,
            text=phrase[1] if phrase else None,
            reason=reply.reason,
            target=reply.target,
            allowed=True,
        )
    else:
        auto_reply = AutoReplySuggestion(
            reason=quiet_reason,
            allowed=outcome.suggested_action.value not in ("escalate_to_human_now",),
        )

    return ProcessResponse(
        session_id=req.session_id,
        detected_language=language,
        language_probability=language_probability,
        transcript_original=original,
        transcript_english=english,
        predictions=outcome.predictions,
        suggested_form=outcome.form,
        suggested_action=outcome.suggested_action,
        missing_fields=outcome.missing_fields,
        requires_review=True,
        latency_ms=latency,
        engine={
            "decision": laya_result.engine if laya_result else rule_engine.name,
            "decision_model": laya_result.model if laya_result else "regex+keywords",
            "whisper_size": req.whisper_size,
            "whisper_device": whisper_engine.device,
            "asr_confidence": "measured" if word_confidence is not None else "not_applicable",
        },
        notes=outcome.prompts + notes,
        calibration_warning=settings.CALIBRATION_WARNING,
        auto_reply=auto_reply,
    )


@app.post("/api/sessions/{session_id}/confirm")
def confirm(session_id: str, payload: dict) -> dict:
    """Record a dispatcher's confirmed or corrected values for a session.

    Stored values are returned to every later ``/api/process`` call for the
    session and are never overwritten by a model pass.
    """
    fields = payload.get("fields") or {}
    if not isinstance(fields, dict):
        raise HTTPException(status_code=400, detail="fields must be an object")
    store = SESSIONS.setdefault(session_id, {})
    store.update(fields)
    return {"session_id": session_id, "confirmed": store}


@app.delete("/api/sessions/{session_id}")
def reset_session(session_id: str) -> dict:
    SESSIONS.pop(session_id, None)
    return {"session_id": session_id, "confirmed": {}}


# Finished Dial calls, keyed by call id. In-process like SESSIONS: a demo
# result does not need to outlive a restart.
DIAL_RESULTS: dict[str, ProcessResponse] = {}


def _dial_reply_audio(phrase_id: str) -> bytes | None:
    """WAV bytes for an approved phrase, or None if Piper is unavailable."""
    result = tts.speak(phrase_id)
    if not result or not result.get("spoken"):
        return None
    return base64.b64decode(result["audio_wav_base64"])




@app.websocket("/dispatchiq/audio/{call_id}")
async def dial_socket(websocket: WebSocket, call_id: str) -> None:
    """Dial Self-Hosted audio socket. Refuses every connection until a signing
    secret is configured.

    The signature is checked before the handshake is accepted. The call then
    runs through ``CallLoop``: caller audio is buffered, approved replies are
    spoken with barge-in, and the buffered audio goes through the pipeline once
    the call ends.
    """
    if not settings.dial_signing_secret:
        await websocket.close(code=1008)
        return
    if not verify_signature(
        websocket.headers.get("x-dial-signature"),
        call_id,
        settings.dial_signing_secret,
        tolerance_s=settings.dial_signature_tolerance_s,
    ):
        log.warning("rejected Dial socket for call %s: bad signature", call_id)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    session = DialSession(call_id=call_id)
    # One review session per call, opened before the first word so the
    # dashboard has somewhere to put the conversation as it arrives rather
    # than only after the caller hangs up.
    bridge = bridge_for(call_id)
    loop = CallLoop(
        session,
        send=websocket.send_text,
        synthesize=_dial_reply_audio,
        opening_phrase=settings.dial_opening_phrase,
        decide_every_s=settings.dial_decide_every_s,
        speech_pause_s=settings.dial_speech_pause_s,
        speech_rms=settings.dial_speech_rms,
        # The same tick transcribes, files a segment, and decides whether to
        # speak -- so what the agent says and what the operator sees come
        # from one pass over one transcript.
        choose_reply=bridge.choose_reply,
        should_end=bridge.should_end_call,
    )
    try:
        await loop.run(websocket.receive_text)
    except WebSocketDisconnect:
        pass
    log.info(
        "Dial call %s finished: %.1fs of caller audio buffered",
        call_id,
        session.buffered_seconds(),
    )
    close_bridge(call_id)
    if session.pcm16k:
        # A final pass over the whole call. The live session already holds the
        # running record; this is the v1-shaped result the existing dashboard
        # and /api/dispatchiq/calls/{id} expect, kept so nothing that worked
        # before stops working.
        await _analyse_dial_call(call_id, bytes(session.pcm16k))


async def _analyse_dial_call(call_id: str, pcm16k: bytes) -> None:
    """Run a finished call's audio through the normal pipeline and keep it.

    The result is stored in memory, keyed by call id, for the dashboard to
    fetch. A failure is logged and leaves no result behind, so the GET endpoint
    returns 404 rather than a partial answer.
    """
    req = ProcessRequest(session_id=call_id)
    try:
        DIAL_RESULTS[call_id] = await _run_pipeline(req, pcm16_to_wav(pcm16k))
    except HTTPException as exc:
        log.warning("Dial call %s: pipeline refused the audio (%s)", call_id, exc.detail)
    except Exception:
        log.exception("Dial call %s: pipeline failed", call_id)


def _approved_text(phrase_id: str) -> str:
    """The exact approved words for a phrase id. Nothing else is ever spoken."""
    return tts.APPROVED[phrase_id][1]


async def _llm_brain(session: LlmSession) -> str | None:
    """Pick the next approved phrase from what the caller has said so far.

    The pipeline runs on the transcript text with the rules engine, so the
    Laya model never loads on this path. Its reply policy supplies the phrase.
    Silence is never the answer on a live call, so a fallback question is used
    when the policy declines.
    """
    text = session.caller_text()
    if not text:
        return "greeting_en"
    req = ProcessRequest(
        session_id=f"llm:{session.call_id}",
        transcript=text,
        decision_engine="rules",
    )
    result = await _run_pipeline(req, None)
    auto = result.auto_reply
    if auto is not None and auto.allowed and auto.phrase_id in tts.APPROVED:
        return auto.phrase_id
    return "what_happening_en"


@app.websocket("/dispatchiq/llm/{call_id}")
async def dial_llm_socket(websocket: WebSocket, call_id: str) -> None:
    """Dial Self-Hosted LLM socket. Refuses every connection until the LLM
    signing secret is configured, and checks the signature before accepting."""
    if not settings.dial_llm_signing_secret:
        await websocket.close(code=1008)
        return
    if not verify_signature(
        websocket.headers.get("x-dial-signature"),
        call_id,
        settings.dial_llm_signing_secret,
        tolerance_s=settings.dial_signature_tolerance_s,
    ):
        log.warning("rejected Dial LLM socket for call %s: bad signature", call_id)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    session = LlmSession(call_id=call_id)
    try:
        while not session.ended:
            raw = await websocket.receive_text()
            for outgoing in await handle_frame(session, raw, _llm_brain, _approved_text):
                await websocket.send_text(outgoing)
    except WebSocketDisconnect:
        pass
    log.info(
        "Dial LLM call %s finished after %d transcript entries",
        call_id,
        len(session.transcript),
    )


@app.get("/api/dispatchiq/calls/{call_id}", response_model=ProcessResponse)
def dial_call_result(call_id: str) -> ProcessResponse:
    result = DIAL_RESULTS.get(call_id)
    if result is None:
        raise HTTPException(status_code=404, detail="no result for this call yet")
    return result
