# API

Base URL `http://127.0.0.1:8000`. The schema in `backend/app/schemas.py` is the
source of truth; this page explains the parts that are not obvious from it.

## `POST /api/process`

Takes **either** a WAV upload or a transcript, and returns the structured,
reviewable result.

**Transcript input** — `application/json`:

```json
{ "session_id": "demo-1", "transcript": "There is smoke coming from my kitchen.",
  "whisper_size": "small", "confirmed": {} }
```

**Audio input** — `multipart/form-data` with two parts: `payload` (the same
JSON, as a string) and `audio` (a WAV file).

### Response

| Field | Meaning |
| --- | --- |
| `transcript_original` / `transcript_english` | What the caller said, and Whisper's translation. Equal for English calls. |
| `detected_language`, `language_probability` | From Whisper. Below 0.5 the workflow asks for an interpreter instead of trusting the fields. |
| `predictions[]` | One per field — see below. |
| `suggested_form` | The same values as a form object, ready to render. |
| `suggested_action` | What to do next. **No member of this enum dispatches anything.** |
| `missing_fields` | Critical fields still unknown. |
| `requires_review` | Always `true`. There is no code path that sets it false. |
| `latency_ms` | Per stage. `network` and `end_to_end` are `null` from the server and filled in by the client, which is the only side that can measure them. |
| `calibration_warning` | Always present. See below. |

### A prediction

```json
{
  "label": "incident_type",
  "value": "structure_fire",
  "score": 0.855,
  "evidence": { "text": "There is smoke coming from my kitchen",
                "start": 0, "end": 37, "source": "original", "match": "exact" },
  "verification_state": "unverified",
  "conflict": false,
  "alternative": null,
  "probabilities": { "structure_fire": 0.867, "medical": 0.101, "...": 0.0 },
  "raw_probability": 0.867,
  "breakdown": { "laya_original": 0.867, "laya_english": 0.84,
                 "cross_check": "agree", "rule_check": "agree",
                 "asr_factor": 0.964, "final": 0.855 }
}
```

- `evidence` is either a span whose offsets slice back to exactly `text` in the
  named transcript, or `null`. It is never absent, so "we found nothing" cannot
  be confused with "we did not look".
- `score` is **derived**, not reported by any model — see `backend/app/confidence.py`.
- `raw_probability` is what the model actually said, kept separate so the two
  are never conflated.
- `alternative` carries the other checker's answer whenever they disagreed.

### `suggested_action`

Evaluated in order, first match wins:

| Action | When |
| --- | --- |
| `await_more_audio` | VAD found no speech. Nothing is inferred from silence. |
| `request_language_support` | `language_probability < 0.5`. |
| `escalate_to_human_now` | A weapon is indicated, or the caller says they are trapped. |
| `request_missing_critical_field` | `incident_type`, `location_text` or `callback_number` is unknown. |
| `request_human_confirmation` | Everything present, nothing confirmed yet. |
| `ready_for_human_submission` | Every critical field confirmed **by a human**. Unreachable from model output. |

## `POST /api/sessions/{id}/confirm`

```json
{ "fields": { "incident_type": "structure_fire", "callback_number": "555-0147" } }
```

Confirmed values are stored per session and returned by every later
`/api/process` call for that session, with `verification_state` set to
`confirmed` or `corrected`. A later model pass **cannot** overwrite them; where
it disagrees, its value appears in `alternative` and `conflict` is set.

`DELETE /api/sessions/{id}` clears them.

## `GET /api/health`

Reports the resolved Whisper device and compute type, whether Laya loaded, how
many TTS phrases are rendered, and the calibration warning. Use it to prove the
demo is on the GPU rather than silently on CPU.

## `GET /api/models`

What the UI may offer: Whisper sizes, which are currently resident, the
decision engines, and the live abstention thresholds.

## `GET /api/phrases` and `POST /api/speak`

`speak` takes `{"phrase_id": "..."}` and nothing else. There is no endpoint
that synthesises arbitrary text. If Piper is unavailable the response carries
the phrase text and `spoken: false` so the dispatcher can read it aloud.

## About the scores

Every response carries `calibration_warning`, and it is not boilerplate. Laya's
shipped checkpoints are over-confident, and loading one prints a warning that
its temperatures are invalid for some option counts. A `0.9` here does **not**
mean 90% correct. The scores are useful for ranking which fields need
attention, not as probabilities. The UI renders them as three bands rather than
decimals for that reason.
