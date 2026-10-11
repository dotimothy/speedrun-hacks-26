"""Dial Self-Hosted LLM mode: our server decides what the agent says.

In this mode Dial does the telephony, speech-to-text and text-to-speech. It
streams the transcript to us, and whenever it wants the agent to speak it sends
``response_required``. We answer with ``response`` text, and Dial speaks it.
That leaves the decision, the words, and the audit trail with this project.

Only approved phrases are ever returned. ``reply_text`` takes a phrase id from
the brain and never free text, so nothing outside ``tts.APPROVED`` reaches the
caller.

Frame shapes follow Dial's LLM protocol: snake_case fields, a ``type`` tag on
every frame, and ``ping_pong`` echoed back unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

log = logging.getLogger("dispatchiq.dial.llm")

# The brain sees the session and returns an approved phrase id, or None to stay
# silent for this turn.
Brain = Callable[["LlmSession"], Awaitable[str | None]]


@dataclass
class LlmSession:
    call_id: str
    direction: str | None = None
    caller: str | None = None
    callee: str | None = None
    instruction: str | None = None
    language: str | None = None
    transcript: list[dict] = field(default_factory=list)
    ended: bool = False

    def caller_text(self) -> str:
        """Everything the caller has said, joined in order."""
        return " ".join(
            entry.get("content", "").strip()
            for entry in self.transcript
            if entry.get("role") == "user" and entry.get("content")
        )


def response_frames(response_id: str, text: str) -> list[str]:
    """One complete ``response`` frame. Dial speaks the text as given."""
    return [
        json.dumps(
            {
                "type": "response",
                "response_id": response_id,
                "content": text,
                "content_complete": True,
            }
        )
    ]


async def handle_frame(session: LlmSession, raw: str, brain: Brain, phrase_text) -> list[str]:
    """Process one frame from Dial and return the frames to send back.

    ``phrase_text`` maps an approved phrase id to its words. It is passed in so
    the protocol stays free of the TTS module.
    """
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("dropping non-JSON frame on call %s", session.call_id)
        return []

    kind = frame.get("type")
    if kind == "call_connected":
        session.direction = frame.get("direction")
        session.caller = frame.get("from")
        session.callee = frame.get("to")
        session.instruction = frame.get("instruction")
        session.language = frame.get("language")
        return []
    if kind == "transcript_update":
        session.transcript = list(frame.get("transcript") or [])
        return []
    if kind == "response_required":
        session.transcript = list(frame.get("transcript") or session.transcript)
        response_id = frame.get("response_id", "")
        phrase_id = await brain(session)
        if phrase_id is None:
            return []
        return response_frames(response_id, phrase_text(phrase_id))
    if kind == "reminder_required":
        # The caller has gone quiet. A holding line is enough to prompt them.
        response_id = frame.get("response_id", "")
        return response_frames(response_id, phrase_text("stay_on_line_en"))
    if kind == "ping_pong":
        return [raw]
    if kind == "call_ended":
        session.ended = True
        return []
    return []
