"""HTTP surface for live sessions (API v2).

Mounted from ``main.py`` with a single ``include_router`` line, deliberately:
a teammate's Dial adapter is editing that file on another branch, and a large
diff there would guarantee a conflict over something neither change cares
about.

Two properties of this module are worth stating because they are enforced by
what it imports rather than by what it promises:

**Review actions never run inference.** The accept, edit, reject, resolve,
publish and read handlers touch ``store``, ``narrative`` and nothing else. No
Whisper, no Laya. ``inference_ran: false`` ships on those responses as a
wire-level assertion of it, and a test patches both engines to raise.

**Nothing reaches the receiving view without a human.** ``/dispatch/inbox``
reads ``snapshot.published``, which only ``publish`` ever appends to.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..asr import whisper_engine
from ..config import settings
from ..confidence import resolve as resolve_confidence
from ..decision.registry import get_engine
from ..decision.rules import RuleEngine
from .diffing import build_suggestions
from .models import (
    AppendSegmentRequest,
    Diagnostics,
    NarrativeUpdate,
    Presence,
    PublishRequest,
    ReviewAction,
    Segment,
    SessionSnapshot,
    Speaker,
)
from . import narrative as narrative_builder
from .store import SessionNotFound, StaleAction, store

router = APIRouter(prefix="/api/v2", tags=["live"])

rule_engine = RuleEngine()


def laya_engine():
    """The configured decision engine.

    Named for history rather than for the implementation: tests patch this.
    Which engine answers is a deployment decision, and the rest of this module
    is written so it cannot tell the difference.
    """
    return get_engine()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _snapshot(session_id: str) -> SessionSnapshot:
    try:
        return store.get(session_id)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail=f"no session {session_id}")


def _conflict(error: StaleAction, session_id: str) -> JSONResponse:
    """409 carrying the authoritative state, so a losing client can re-render.

    Returning the whole snapshot costs nothing from an in-memory store and
    saves the client a follow-up GET -- and a follow-up GET is another chance
    to race.
    """
    snapshot = store.get(session_id)
    return JSONResponse(
        status_code=409,
        content={
            "error": error.code,
            "reason": error.reason,
            "applied": False,
            "current_revision": error.current_revision,
            "suggestion_status": (
                error.suggestion_status.value if error.suggestion_status else None
            ),
            "snapshot": json.loads(snapshot.model_dump_json()),
        },
    )


def _refresh_narrative(session_id: str) -> None:
    snapshot = store.get(session_id)
    store.regenerate_narrative(
        session_id,
        narrative_builder.render(
            snapshot.accepted_record,
            unresolved=[item.text for item in snapshot.unresolved],
            open_flags=[flag.text for flag in snapshot.open_flags],
        ),
    )


def _review_response(session_id: str, snapshot: SessionSnapshot) -> dict:
    _refresh_narrative(session_id)
    return {
        "applied": True,
        # Structural claim, not a courtesy: this handler imported no engine.
        "inference_ran": False,
        "snapshot": json.loads(store.get(session_id).model_dump_json()),
    }


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------
@router.post("/sessions", status_code=201)
def create_session(payload: dict | None = None) -> dict:
    session_id = (payload or {}).get("session_id")
    snapshot = store.create(session_id)
    _refresh_narrative(snapshot.session_id)
    return json.loads(store.get(snapshot.session_id).model_dump_json())


@router.get("/sessions")
def list_sessions() -> dict:
    """Every call this process has seen, newest first.

    Summaries rather than ids: a call history is only useful if you can tell
    the calls apart without opening each one, and the three things that
    identify a call to an operator are what it was, where it was, and whether
    anything was shared.
    """
    summaries = []
    for session_id in store.ids():
        snapshot = store.get(session_id)
        record = snapshot.accepted_record
        summaries.append(
            {
                "session_id": session_id,
                "created_at": snapshot.created_at,
                "revision": snapshot.revision,
                "segments": len(snapshot.segments),
                "pending": len(snapshot.pending),
                "open_flags": len(snapshot.open_flags),
                "published": len(snapshot.published),
                "language": snapshot.detected_language,
                "incident_type": (
                    record["incident_type"].value if "incident_type" in record else None
                ),
                "location_text": (
                    record["location_text"].value if "location_text" in record else None
                ),
                # The first thing the caller said: what an operator actually
                # recognises a call by.
                "opening_line": (
                    snapshot.segments[0].original_text[:90] if snapshot.segments else None
                ),
            }
        )
    summaries.sort(key=lambda item: item["created_at"], reverse=True)
    return {"sessions": summaries}


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    return json.loads(_snapshot(session_id).model_dump_json())


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    store.delete(session_id)
    return {"session_id": session_id, "deleted": True}


# ---------------------------------------------------------------------------
# appending an utterance
# ---------------------------------------------------------------------------
@router.post("/sessions/{session_id}/segments")
async def append_segment(session_id: str, request: Request) -> dict:
    snapshot = _snapshot(session_id)
    started = time.perf_counter()

    content_type = request.headers.get("content-type", "")
    audio_bytes: bytes | None = None
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw = form.get("payload")
        payload = json.loads(raw) if isinstance(raw, str) else {}
        upload = form.get("audio")
        audio_bytes = await upload.read() if hasattr(upload, "read") else None
        req = AppendSegmentRequest(**payload)
    else:
        req = AppendSegmentRequest(**(await request.json()))

    if store.seen_client_segment(session_id, req.client_segment_id):
        # A reconnect replaying an utterance already recorded.
        return {
            "idempotent_replay": True,
            "snapshot": json.loads(snapshot.model_dump_json()),
        }

    original = (req.text or "").strip()
    english = original
    language = req.language
    language_probability = None
    word_confidence = None
    asr_ms = 0.0
    start_ms, end_ms = req.start_ms, req.end_ms

    if audio_bytes:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(audio_bytes)
            path = handle.name
        try:
            t0 = time.perf_counter()
            orig_t, eng_t = whisper_engine.transcribe_and_translate(path, req.whisper_size)
            asr_ms = (time.perf_counter() - t0) * 1000
            if orig_t is None:
                raise HTTPException(status_code=503, detail="ASR unavailable")
            original = orig_t.text.strip()
            english = (eng_t.text if eng_t else original) or original
            language = orig_t.language
            language_probability = orig_t.language_probability
            word_confidence = orig_t.word_confidence
            if orig_t.segments and start_ms is None:
                # Whisper reports float seconds relative to the clip; the
                # stored value is milliseconds from session creation.
                base = req.start_ms or 0
                start_ms = base + round(orig_t.segments[0].start * 1000)
                end_ms = base + round(orig_t.segments[-1].end * 1000)
        finally:
            os.unlink(path)

    if not original:
        raise HTTPException(status_code=400, detail="segment has no text")

    segment = Segment(
        id=store.next_id(session_id, "seg"),
        index=len(snapshot.segments),
        speaker=req.speaker,
        original_text=original,
        language=language,
        translation=english if english and english != original else None,
        start_ms=start_ms,
        end_ms=end_ms,
        final=req.final,
        client_segment_id=req.client_segment_id,
    )

    # Inference runs over the caller's words so far, not this segment alone:
    # "Eastbound side" means nothing on its own, and Laya costs ~40ms, so
    # there is no reason to trade accuracy for it. Operator and interpreter
    # speech is excluded -- only the caller's statements become evidence.
    caller_text = " ".join(
        s.original_text for s in [*snapshot.segments, segment] if s.speaker is Speaker.CALLER
    )
    caller_english = " ".join(
        (s.translation or s.original_text)
        for s in [*snapshot.segments, segment]
        if s.speaker is Speaker.CALLER
    )

    engine = get_engine(req.decision_engine)
    t0 = time.perf_counter()
    laya_result = engine.extract(caller_text, caller_english, language)
    rule_result = rule_engine.extract(caller_text, caller_english, language)
    resolution = resolve_confidence(
        laya_result, rule_result, caller_text, caller_english, asr_confidence=word_confidence
    )
    decision_ms = (time.perf_counter() - t0) * 1000

    outcome = build_suggestions(
        accepted=snapshot.accepted_record,
        pending=snapshot.pending,
        predictions=resolution.predictions,
        segment=segment,
        base_revision=snapshot.revision,
        next_id=lambda prefix: store.next_id(session_id, prefix),
    )

    diagnostics = Diagnostics(
        last_asr_ms=round(asr_ms, 1),
        last_decision_ms=round(decision_ms, 1),
        last_total_ms=round((time.perf_counter() - started) * 1000, 1),
        engine={
            "decision": laya_result.engine,
            "decision_model": laya_result.model,
            # Where the caller's words actually went. Reported on every
            # response, not just in health, because it can change per request.
            "inference_location": engine.describe().get("inference_location", "unknown"),
            "is_local": str(getattr(engine, "is_local", True)),
            "whisper_size": req.whisper_size,
            "whisper_device": whisper_engine.device,
            "asr_confidence": "measured" if word_confidence is not None else "not_applicable",
        },
        calibration_warning=settings.CALIBRATION_WARNING,
        notes=list(laya_result.notes),
    )

    store.commit_append(
        session_id,
        segment,
        outcome.suggestions,
        outcome.flags,
        outcome.unresolved,
        superseded=outcome.superseded,
        corroborated=outcome.corroborated,
        detected_language=language,
        language_probability=language_probability,
        diagnostics=diagnostics,
    )
    _refresh_narrative(session_id)

    return {
        "idempotent_replay": False,
        "segment": json.loads(segment.model_dump_json()),
        "new_suggestion_ids": [s.id for s in outcome.suggestions],
        "superseded": outcome.superseded,
        "snapshot": json.loads(store.get(session_id).model_dump_json()),
    }


# ---------------------------------------------------------------------------
# review actions -- no engine is imported into any of these paths
# ---------------------------------------------------------------------------
@router.post("/sessions/{session_id}/suggestions/{suggestion_id}/accept")
def accept(session_id: str, suggestion_id: str, action: ReviewAction):
    _snapshot(session_id)
    try:
        snapshot = store.accept(
            session_id, suggestion_id,
            base_revision=action.base_revision, operation_id=action.operation_id,
            value=action.value, edited=action.value is not None, note=action.note,
        )
    except StaleAction as error:
        return _conflict(error, session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no suggestion {suggestion_id}")
    return _review_response(session_id, snapshot)


@router.post("/sessions/{session_id}/suggestions/{suggestion_id}/reject")
def reject(session_id: str, suggestion_id: str, action: ReviewAction):
    _snapshot(session_id)
    try:
        snapshot = store.reject(
            session_id, suggestion_id,
            base_revision=action.base_revision, operation_id=action.operation_id,
            reason=action.reason,
        )
    except StaleAction as error:
        return _conflict(error, session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no suggestion {suggestion_id}")
    return _review_response(session_id, snapshot)


@router.post("/sessions/{session_id}/suggestions/{suggestion_id}/resolve")
def resolve(session_id: str, suggestion_id: str, action: ReviewAction):
    """Settle a conflict by naming which statement is right."""
    _snapshot(session_id)
    option_id = action.reason  # the chosen option id travels in `reason`
    try:
        snapshot = store.resolve(
            session_id, suggestion_id,
            base_revision=action.base_revision, operation_id=action.operation_id,
            option_id=option_id, value=action.value, note=action.note,
        )
    except StaleAction as error:
        return _conflict(error, session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no suggestion {suggestion_id}")
    return _review_response(session_id, snapshot)


@router.post("/sessions/{session_id}/fields/{field_name}")
def set_field(session_id: str, field_name: str, payload: dict):
    """Operator entry with no suggestion behind it.

    The route by which "the caller has no phone" gets recorded -- a fact no
    model proposes, and the reason ``Presence`` exists.
    """
    _snapshot(session_id)
    presence = Presence(payload.get("presence", "stated"))
    snapshot = store.set_field_directly(
        session_id, field_name,
        value=payload.get("value"), presence=presence,
        operation_id=payload.get("operation_id") or f"direct-{time.time_ns()}",
        note=payload.get("note"),
    )
    return _review_response(session_id, snapshot)


@router.post("/sessions/{session_id}/flags/{flag_id}/acknowledge")
def acknowledge(session_id: str, flag_id: str) -> dict:
    _snapshot(session_id)
    store.acknowledge_flag(session_id, flag_id)
    return json.loads(store.get(session_id).model_dump_json())


# ---------------------------------------------------------------------------
# narrative and publication
# ---------------------------------------------------------------------------
@router.put("/sessions/{session_id}/narrative")
def set_narrative(session_id: str, update: NarrativeUpdate) -> dict:
    _snapshot(session_id)
    store.set_narrative(session_id, update.text)
    return json.loads(store.get(session_id).model_dump_json())


@router.post("/sessions/{session_id}/publish", status_code=201)
def publish(session_id: str, request: PublishRequest):
    _snapshot(session_id)
    _refresh_narrative(session_id)
    try:
        _, published = store.publish(
            session_id,
            base_revision=request.base_revision,
            operation_id=request.operation_id,
        )
    except StaleAction as error:
        return _conflict(error, session_id)
    return {
        "published": json.loads(published.model_dump_json()),
        # Said on the wire because it is the whole point of this endpoint's
        # existence: publishing shares a record, it does not send a unit.
        "routed": "no",
        "snapshot": json.loads(store.get(session_id).model_dump_json()),
    }


@router.get("/dispatch/inbox")
def inbox() -> dict:
    """The simulated receiving dispatcher.

    Reads only what was published. It is structurally unable to see a live
    record, so "appears only after an operator action" is a property of the
    data flow rather than a rule someone has to remember.
    """
    items = []
    for session_id in store.ids():
        snapshot = store.get(session_id)
        for published in snapshot.published:
            items.append(
                {
                    "session_id": session_id,
                    **json.loads(published.model_dump_json()),
                    "routed": "no",
                }
            )
    items.sort(key=lambda item: item["published_at"])
    return {"items": items, "note": "Simulated receiving view. Nothing here was dispatched."}


@router.get("/dispatch/inbox/{session_id}")
def inbox_for(session_id: str) -> dict:
    snapshot = _snapshot(session_id)
    if not snapshot.published:
        raise HTTPException(status_code=404, detail="nothing published for this session")
    return {
        "session_id": session_id,
        "publications": [json.loads(p.model_dump_json()) for p in snapshot.published],
        "routed": "no",
    }
