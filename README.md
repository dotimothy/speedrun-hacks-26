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

## Running it

```bash
python scripts/setup.py --install    # macOS/Linux: python3
python -m uvicorn app.main:app --app-dir backend --port 8000
cd frontend && npm run dev           # http://localhost:5173
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
python -m pytest -q        # 67 contract tests, no GPU required
python scripts/smoke.py    # end-to-end against a running backend
python scripts/bench.py    # writes docs/LATENCY.md
```

`DISPATCHIQ_MOCK=1` serves the committed fixture responses with no models
loaded at all — the demo fallback.

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
[`docs/LATENCY.md`](./docs/LATENCY.md).

## Status

Working prototype on `feat/full-prototype`. `plan.md` holds the sprint plan and
role ownership; [`docs/DEMO.md`](./docs/DEMO.md) is the run sheet;
[`docs/API.md`](./docs/API.md) documents the contract.

## Team

Three-person SpeedHacks team: AI/backend, systems/performance, product/UX.
Ownership breakdown in [`plan.md`](./plan.md#team-ownership).

## License

Apache 2.0 — see [LICENSE](./LICENSE).
