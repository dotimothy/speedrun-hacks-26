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
Browser mic / WAV / Dial call  →  audio transport  →  Whisper (multilingual)
                                                          │  original + English transcript
                                                          ▼
                                          decision engine (Laya by default)
                                                          │  typed answers + confidence + evidence
                                                          ▼
                                  diffing + deterministic workflow engine
                                                          │
                                                          ▼
                       suggested incident form  →  HUMAN REVIEW  →  publish
```

Reply path: an approved template phrase → Piper TTS → browser speakers, or → Dial,
which speaks it on a live call.

All inference runs on hardware the team controls by default — `faster-whisper` for ASR
and translation, Laya for structured extraction. Two engines (OpenAI and TypeSafe Jev) can
send a caller's words to a third party; they are off unless explicitly enabled. See
[Cloud engines](#cloud-engines).

---

## What is implemented

### Batch processing (API v1)

- `POST /api/process` accepts a WAV **or** a plain transcript and returns structured JSON:
  both transcripts, typed predictions with scores and evidence, and a suggested form.
- Whisper transcription with English translation; language is auto-detected.
- Confidence is derived from independent checks, not read from the model. See
  [How the scores are built](#why-the-scores-are-built-rather-than-read).
- Confirm and reject endpoints (`/api/sessions/{id}/confirm`, `DELETE /api/sessions/{id}`).
- Mock mode (`DISPATCHIQ_MOCK=1`) serves committed fixture responses with no models loaded.

### Live sessions (API v2)

A session accumulates transcript segments as a call progresses, and the system proposes
field updates as a diff against what has already been accepted.

- `POST /api/v2/sessions`, `GET /api/v2/sessions`, `GET`/`DELETE /api/v2/sessions/{id}`.
- `POST /api/v2/sessions/{id}/segments` appends an utterance (JSON or multipart with audio).
  Replays are idempotent via `client_segment_id`.
- Each update is classified as an **addition**, **correction**, **conflict**, or
  **confirmation**. A conflict is shown to the operator, not resolved by the system.
- Suggestions can be accepted, rejected, or resolved (`…/suggestions/{id}/accept|reject|resolve`),
  and fields can be set directly (`…/fields/{name}`).
- Review flags (weapon mentioned, caller may be trapped, language uncertain, and others)
  can be acknowledged.
- A narrative draft is generated from a template and can be edited (`PUT …/narrative`).
- `POST …/publish` shares a snapshot to the receiving view. `GET /api/v2/dispatch/inbox`
  shows only published records; live records are not visible there.
- Actions carry an `operation_id` (double-submit protection) and a `base_revision`
  (stale-decision protection). Review actions never run inference; this is tested.

Session state is held in process memory (`backend/app/live/store.py`). It is lost on restart.

### Decision engines

Selected by `DISPATCHIQ_DECISION_ENGINE` or per request. Engines are cached per process.

| Engine | Where inference runs | Status |
|---|---|---|
| `laya` | This machine (PyTorch, MPS/CUDA/CPU) | Default. Implemented and measured. |
| `rules` | This machine (regex / keyword) | Implemented. Owns address, callback number, and head-count. |
| `openai` | OpenAI, over the internet | Implemented. Requires `DISPATCHIQ_ALLOW_CLOUD=1` and `OPENAI_API_KEY`. |
| `jev` | TypeSafe System-One (hosted), or a self-hosted endpoint | Implemented. Requires `system-one-adapter`. Not yet verified against the live endpoint. |

A request for a cloud engine on a deployment that hasn't enabled it falls back to a local
engine and says so in the response notes. It never errors and never silently uses the cloud.

An `ollama` value appears in the schema and the `decision/base.py` docstring, and
`DISPATCHIQ_DECISION_MODEL` / `OLLAMA_HOST` exist in config, but **no Ollama engine is
implemented**.

### Audio transports

- **Browser.** Live microphone (`getUserMedia`) and WAV upload. Served over HTTPS.
- **Dial Self-Hosted, audio socket** (`/dispatchiq/audio/{call_id}`). Full-duplex call loop
  with barge-in (queued audio is dropped and a `clear` is sent when the caller starts
  talking), 20 ms frame pacing, and periodic decisions on caller audio. Connections must
  carry a valid HMAC signature (`DIAL_SIGNING_SECRET`).
- **Dial Self-Hosted, LLM socket** (`/dispatchiq/llm/{call_id}`). Dial handles telephony,
  STT and TTS. This project decides what to say and returns only approved phrase IDs.
  Refuses all connections until `DIAL_LLM_SIGNING_SECRET` is set.
- `scripts/inbound.py` turns inbound calls to the Dial number on, off, or reports status.

Both Dial sockets are covered by tests that use recorded frames. They have not yet been
exercised against live Dial telephony.

### Replies and autonomous mode

- **Piper TTS** speaks pre-approved phrases only (`tts.APPROVED`). No code path synthesises
  arbitrary text.
- **Autonomous replies** (`autoreply.py`) are off by default. When on, the system may ask
  one approved question when a field is missing, with a visible countdown and a stop control.
  Rules are enforced in code and tested: approved phrases only, never during an escalation,
  never the same question twice, never a promise of help, and no writes to the form or
  `requires_review`.

### Dashboard (React + Vite)

- Both transcripts, the prediction list, and an editable suggested form with per-field
  verification state.
- Microphone and WAV input, call audio playback, and an auto-reply panel.
- Settings drawer: decision engine choice, speech recognition model, abstention thresholds,
  and a "this machine" panel reporting what was actually resolved.
- Call history, which lists v2 sessions.

**Not yet in the dashboard:** the v2 review queue (accept, reject, resolve), narrative
editing, and the receiving view. These exist as API endpoints only.

### Tests

`python -m pytest -q` runs **198 tests** covering the v1 contract, autoreply rules, the Dial
transports and LLM socket, the live store, diffing, and the v2 HTTP surface. No GPU or
network access is required.

---

## Explicitly out of scope

- Real call audio, real addresses, or live telephony in the demo.
- Automated urgency, triage, resource allocation, or dispatch. The receiving view is simulated.
- Cloud inference by default.
- Any claim of validated real-world safety or time savings.

---

## API sketch

Batch (v1):

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

Live sessions (v2): `POST /api/v2/sessions`, then `POST /api/v2/sessions/{id}/segments`,
review actions on suggestions and fields, `PUT …/narrative`, and `POST …/publish`.

Full contract: [`docs/API.md`](./docs/API.md). Schema source of truth:
[`backend/app/schemas.py`](./backend/app/schemas.py).

---

## Repo layout

```
/backend
  app/main.py         FastAPI app: v1 routes, Dial sockets, mounts the v2 router
  app/live/           v2 live sessions: store, diffing, narrative, HTTP router
  app/decision/       decision engines: laya, rules, openai, jev; registry
  app/transport/      Dial audio and LLM transports, audio codecs
  app/asr.py          faster-whisper wrapper
  app/tts.py          Piper wrapper and the approved phrase list
  app/autoreply.py    autonomous reply policy
  app/confidence.py   score derivation from independent checks
  app/workflow.py     deterministic workflow engine
  tests/              pytest suite
/frontend   React + Vite dashboard
/fixtures   synthetic call audio, scenarios, and expected JSON responses
/scripts    setup, serve, smoke test, benchmark, Dial inbound toggle
/docs       API contract, demo script, latency results, setup notes
plan.md     team execution plan and sprint timeline
```

## Running it

```bash
python scripts/setup.py --install    # macOS/Linux: python3
python scripts/serve.py              # HTTPS on 0.0.0.0, both servers
```

`setup.py` installs the dependencies, fetches the Piper voices, generates the
demo audio and runs `npm install`; run it without `--install` any time to
check the environment. Full per-platform notes in
[`docs/SETUP.md`](./docs/SETUP.md).

Runs on Windows, macOS and Linux, though not on the same hardware: Whisper
uses CUDA where there is an NVIDIA card and CPU int8 otherwise — CTranslate2
has no Metal backend, so that is the only option on a Mac. Laya is PyTorch and
does use MPS there, so the decision stage stays fast and it is speech
recognition that pays. `/api/health` reports what was actually resolved.

```bash
python -m pytest -q        # 198 tests, no GPU required
python scripts/smoke.py    # end-to-end against a running backend
python scripts/bench.py    # writes docs/LATENCY.md
```

`DISPATCHIQ_MOCK=1` serves the committed fixture responses with no models
loaded at all — the demo fallback.

### Configuration

Set in `.env` at the repo root (see `.env.example`). `.env` is gitignored and must stay so.

| Variable | Default | Purpose |
|---|---|---|
| `DISPATCHIQ_DECISION_ENGINE` | `laya` | Default engine: `laya`, `rules`, `openai`, `jev` |
| `DISPATCHIQ_ALLOW_CLOUD` | `0` | Must be `1` before `openai` or a hosted `jev` can be used |
| `DISPATCHIQ_JEV_ENDPOINT` / `_API_KEY` / `_IS_LOCAL` | — / — / `0` | TypeSafe System-One connection; `_IS_LOCAL=1` marks a self-hosted endpoint as local |
| `DIAL_SIGNING_SECRET` | — | Enables the Dial audio socket |
| `DIAL_LLM_SIGNING_SECRET` | — | Enables the Dial LLM socket |
| `DISPATCHIQ_MOCK` | `0` | Serve fixtures with no models loaded |

## How the pieces are chosen

**Laya** answers the typed questions — one non-autoregressive forward pass,
measured at 35–45 ms for the full question set against 2.3–10.5 s for a
generative model asked the same thing. It also routes by language, so the
same questions can be asked of the caller's own words and of Whisper's
English translation and the answers compared.

**Whisper** (`faster-whisper`) produces both transcripts and, importantly, a
per-word probability — the one confidence signal in the stack that genuinely
moves with input quality.

**A regex/keyword extractor** owns the address, the callback number and the
head-count, because those are patterns rather than judgments, and it acts as
an independent second opinion on everything else.

**Piper** speaks the approved reply phrases. There is no code path that
synthesises arbitrary text.

**Dial** is an alternative transport. It replaces the browser audio path and
nothing else: Whisper, the decision engines and the workflow see the same 16 kHz
PCM either way.

Served over HTTPS on every interface, because `getUserMedia` only runs in a
secure context and a LAN address is not one — without TLS the microphone works
on the demo laptop and silently fails on every other device.

### Cloud engines

`openai` and `jev` send the caller's words to a third party. Both are opt-in:
`DISPATCHIQ_ALLOW_CLOUD=1` must be set, and each engine reports `is_local = false`
in `/api/health` and in every response. A self-hosted Jev endpoint can be marked
`DISPATCHIQ_JEV_IS_LOCAL=1` and does not need the cloud switch. When cloud is
enabled, the "no cloud inference" claim in this README no longer holds for those
requests, and the demo script must say so.

### Why the scores are built rather than read

Every self-reported confidence on this stack was measured and discarded: a
generative model asked for a 0–1 score returned exactly `1.0` every time;
token logprobs under constrained JSON decoding measure grammar friction, not
belief; self-consistency over seven samples agreed 7/7 on every case including
the ambiguous ones. Laya reports real probabilities, but its own documentation
says the shipped checkpoints are over-confident.

So a field's score is derived from agreement between independent methods, and
anything with no locatable support in the transcript and no second opinion is
capped below the abstention threshold and reported as `unknown`.

The clearest demonstration is a real failure the pipeline catches: on the
Spanish kitchen-fire call, Laya's multilingual checkpoint returns `gas_leak`
at **0.956** while the English translation of the same sentence returns
`structure_fire` at **0.867**. Cross-checking turns that into `unknown` with
both candidates shown, instead of telling a dispatcher "gas leak, 96%".

## Measured

Median warm end-to-end **647 ms** per call on an RTX 3090 (Whisper `small`),
of which **65 ms** is the decision engine. Full table in
[`docs/LATENCY.md`](./docs/LATENCY.md). These figures predate the v2 live-session
and Dial work and have not been re-measured since.

## Status

Branch `feat/live-session`. Implemented: batch processing, the v2 live-session API,
the Laya/rules/OpenAI/Jev engines, browser and Dial audio transports, Piper replies,
and autonomous mode. Not yet implemented: the v2 review UI, the receiving view UI,
an Ollama engine, and live Dial telephony verification.

[`plan.md`](./plan.md) holds the sprint plan and role ownership;
[`docs/API.md`](./docs/API.md) documents the contract.

## Team

Three-person SpeedHacks team: AI/backend, systems/performance, product/UX.
Ownership breakdown in [`plan.md`](./plan.md#team-ownership).

## License

Apache 2.0 — see [LICENSE](./LICENSE).
