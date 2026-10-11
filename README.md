# DispatchIQ

**Multilingual call documentation support — local models, human decisions.**

DispatchIQ turns live multilingual conversations into structured, human-reviewable
information using specialized local AI models — reducing repetitive documentation work
without cloud inference.

> ⚠️ **Prototype.** Built for SpeedHacks 2026 on synthetic scenarios and sample addresses.
> DispatchIQ does not route calls, score urgency, allocate resources, or dispatch anything.
> Every output is a *suggestion* that a human confirms, corrects, or discards.

---

## The problem

A dispatcher taking an emergency call does two jobs at once: talk to a person in distress,
and type the same fields into a form — location, callback number, incident type, injuries,
hazards. When the caller doesn't speak the dispatcher's language, a third job appears.
The talking is the part that needs a human. The typing is not.

## The idea

Listen alongside the call. Transcribe it in the caller's language, translate it, and pull
the form fields out of what was actually said — each one tagged with a confidence score and
the quote it came from. Hand the dispatcher a pre-filled draft they can read, fix, and
approve in seconds instead of typing from scratch.

Three properties make it trustworthy rather than merely fast:

- **Evidence-backed.** Every suggested field cites the span of transcript it came from.
- **Uncertainty is explicit.** `unknown` is a distinct value from `false`. The model is
  allowed to not know, and says so rather than guessing.
- **The human is never bypassed.** Confirmed fields are never silently overwritten, and
  nothing leaves the screen without approval.

---

## How it works

```
Browser mic / WAV  →  audio transport  →  Whisper (multilingual)
                                              │  original + English transcript
                                              ▼
                                        Laya decision model
                                              │  typed tags + uncertainty + evidence
                                              ▼
                                     deterministic workflow engine
                                              │
                                              ▼
                                   suggested incident form  →  HUMAN REVIEW
```

Optional reply path: an approved template phrase → Piper TTS → browser speakers.

All inference runs on hardware the team controls — `faster-whisper` for ASR and
translation, Laya for structured extraction. No audio or transcript is sent to a
third-party service.

---

## MVP scope

The eight-hour target. Everything here is in scope; everything else is not.

**In**

- `POST /api/process` — accepts a WAV **or** a plain transcript, returns structured JSON.
- Whisper transcription + English translation, language auto-detected.
- Laya extraction into typed incident tags, each with a score and an evidence span.
- A dashboard showing both transcripts, the tags, and an editable suggested form.
- Confirm / correct / take-over controls, with verification state per field.
- One multilingual scenario working end to end.
- Measured latency per stage, and a manual-vs-assisted form completion comparison.

**Stretch**

- Piper TTS for pre-approved reply phrases, with interruption control.
- Dial as an alternative audio transport (swaps the transport layer only).

**Explicitly out**

- Real call audio, real addresses, live telephony.
- Automated urgency, triage, resource allocation, or dispatch.
- Cloud inference of any kind.
- Any claim of validated real-world safety or time savings.

---

## API sketch

```http
POST /api/process
{ "session_id": "demo-001", "audio": "<base64 wav>" }
```

```json
{
  "session_id": "demo-001",
  "detected_language": "es",
  "transcript_original": "...",
  "transcript_english": "...",
  "predictions": [
    { "label": "incident_type", "value": "structure_fire", "score": 0.91,
      "evidence": "there's smoke coming from the kitchen" },
    { "label": "injuries", "value": "unknown", "score": 0.0, "evidence": null }
  ],
  "suggested_form": { "location": "...", "callback_number": "...", "incident_type": "..." },
  "suggested_action": "request_human_confirmation",
  "requires_review": true,
  "latency_ms": { "asr": 0, "laya": 0, "tts": 0, "network": 0, "end_to_end": 0 }
}
```

Full contract and schema rules: [`plan.md`](./plan.md).

---

## Repo layout

```
/backend    FastAPI app, Whisper + Laya wrappers, workflow engine
/frontend   React + Vite dashboard
/fixtures   synthetic call audio + expected JSON responses
/docs       API contract, demo script, latency results
plan.md     team execution plan and sprint timeline
```

## Status

Pre-build. `plan.md` holds the sprint plan, role ownership, and risk fallbacks.

## Team

Three-person SpeedHacks team: AI/backend, systems/performance, product/UX.
Ownership breakdown in [`plan.md`](./plan.md#team-ownership).

## License

Apache 2.0 — see [LICENSE](./LICENSE).
