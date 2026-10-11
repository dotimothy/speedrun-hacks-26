# DispatchIQ — SpeedHacks Team Execution Plan

**Date:** October 11, 2026 · **Team size:** 3 · **Scope:** local-first prototype

---

## Mission

Build a multilingual, low-latency decision-support prototype that converts simulated
emergency-call audio into structured, human-reviewable incident information. Prove
administrative time savings **without** automating actual dispatch or emergency response.

## Pitch

> DispatchIQ turns live multilingual conversations into structured, human-reviewable
> information using specialized local AI models — reducing repetitive documentation work
> without cloud inference.

## Non-goals (hard boundaries)

- No real 911 audio, no real addresses, no live call routing.
- No automated urgency scoring, resource allocation, or dispatch decisions.
- No cloud inference. All models run on the team's remote Windows RTX 3090.
- No claims of real-world safety or validated time savings in the demo narrative.

---

## Team ownership

### AI / Backend Lead — *Timothy*
- Deploy `faster-whisper` and Laya on the remote Windows RTX 3090.
- Implement typed incident tags, uncertainty representation, and form suggestions.
- Build `FastAPI` `POST /api/process` accepting WAV **or** transcript input.
- **Deliver a stable API schema and real example outputs early** — this unblocks the other two.

### Systems / Performance Lead — *Apple chip designer*
- Browser microphone capture, PCM normalization, buffering, audio transport abstraction.
- Piper TTS integration with pre-cached approved phrases and interruption controls.
- Latency instrumentation: ASR, Laya, TTS, network, end-to-end.
- Optional Dial adapter — **only after the core demo works**.

### Product / UX Lead — *Mobile photobooth founder*
- React + Vite dashboard, built first against mock API responses.
- Surface original + English transcripts, evidence-backed tags, editable form fields,
  verification status, and human takeover.
- Own product narrative, UI polish, slides, and demo rehearsal.

---

## Architecture

```
Browser microphone / WAV
        │
        ▼
Audio transport adapter ──────────────── (optional: Dial replaces ONLY this layer)
        │
        ▼
Multilingual Whisper  →  original transcript + English translation
        │
        ▼
Laya decision model   →  typed tags + uncertainty + evidence spans
        │
        ▼
Deterministic workflow engine
        │
        ▼
Suggested incident form  →  HUMAN REVIEW (confirm / correct / take over)
```

**Optional reply path:** approved template → Piper TTS → browser speakers.

Core inference stays on the RTX 3090. Dial integration is subject to access and
bidirectional streaming support; it substitutes for the transport layer and nothing else.

---

## API contract

`POST /api/process`

**Request**

```json
{
  "session_id": "demo-001",
  "audio": "<base64 wav>",
  "transcript": "optional — use instead of audio"
}
```

**Response**

```json
{
  "session_id": "demo-001",
  "detected_language": "es",
  "transcript_original": "...",
  "transcript_english": "...",
  "predictions": [
    { "label": "incident_type", "value": "structure_fire", "score": 0.91, "evidence": "there's smoke coming from the kitchen" },
    { "label": "injuries",      "value": "unknown",        "score": 0.0,  "evidence": null }
  ],
  "suggested_form": { "location": "...", "callback_number": "...", "incident_type": "..." },
  "suggested_action": "request_human_confirmation",
  "requires_review": true,
  "latency_ms": { "asr": 0, "laya": 0, "tts": 0, "network": 0, "end_to_end": 0 }
}
```

**Schema rules**

- `unknown` is a distinct value from `false`. Never collapse the two.
- Confirmed fields are **never** silently overwritten by a later model pass.
- Every prediction carries a `score` and an `evidence` span (or explicit `null`).
- `requires_review` defaults to `true`; nothing is actionable without a human.

---

## Repo layout (proposed)

```
/backend      FastAPI app, Whisper + Laya wrappers, workflow engine
/frontend     React + Vite dashboard
/fixtures     synthetic call audio (WAV) + expected JSON responses
/docs         API contract, demo script, latency results
plan.md
```

**Branches:** `main` stays demo-ready. Work on `feat/backend-api`,
`feat/audio-transport`, `feat/dashboard`; integrate via PR into `main`.

---

## Eight-hour sprint

| Window | Focus |
| --- | --- |
| 0 – 0.5h | Agree on API contract, synthetic demo script, Git branches, ownership |
| 0.5 – 2h | Parallel build: model API, audio transport, mocked dashboard |
| 2 – 3.5h | Integrate Whisper, Laya, browser audio |
| 3.5 – 5h | Connect live backend to dashboard; test form corrections |
| 5 – 6h | Multilingual example, Piper TTS, latency instrumentation |
| 6 – 7h | Optional Dial integration — **only if core demo is stable** |
| 7 – 8h | Benchmark, rehearse, record fallback video, stabilize |

**Gate at 2h:** committed `fixtures/*.json` example responses must exist, or the dashboard
stays on mocks and the backend scope shrinks.

---

## Definition of done

- [ ] Microphone or prerecorded call transcribes.
- [ ] Laya returns structured tags with uncertainty.
- [ ] Dashboard displays real predictions and editable suggested fields.
- [ ] Human can confirm, correct, and take over; no automatic dispatch.
- [ ] At least one multilingual scenario works end to end.
- [ ] Model latency measured; manual-vs-assisted form completion time measured.
- [ ] Backup demo recorded.

---

## 90-second judging demo

| Time | Who | Beat |
| --- | --- | --- |
| 0 – 15s | Product | The repetitive documentation burden |
| 15 – 40s | Systems | Play simulated multilingual call |
| 40 – 65s | AI | Whisper → Laya → pre-filled form |
| 65 – 80s | Product | Verify information, approve optional TTS reply |
| 80 – 90s | AI | Measured latency + controlled time-saving comparison |

---

## Risk controls and fallback

| Risk | Fallback |
| --- | --- |
| Remote GPU unavailable | Smaller local classifier, or clearly labeled replay of recorded output |
| Dial access / streaming fails | Browser audio transport (the default path anyway) |
| Piper TTS fails | Show the suggested reply text instead of speaking it |
| Whisper too slow for live | Pre-recorded WAV path; `transcript` input bypasses ASR entirely |
| Integration slips past 5h | Dashboard reverts to committed fixture responses for the demo |

**Standing constraints:** synthetic scenarios and sample addresses only; never route real
911 calls; never automate urgency, resource allocation, or dispatch; do not claim
real-world safety or time savings without validation.
