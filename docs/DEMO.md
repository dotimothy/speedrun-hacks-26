# Running the demo

## Start it

```bash
# backend  (first run downloads Whisper + Laya weights)
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

# dashboard
cd frontend && npm install && npm run dev     # http://localhost:5173
```

Check `http://127.0.0.1:8000/api/health` first. It should report a real device
and `laya.ready: true`. Demo from `localhost` — microphone capture needs a
secure context and a LAN IP is not one.

Before going on stage:

```bash
python -m pytest -q          # 67 contract tests, no GPU needed
python scripts/smoke.py      # end-to-end against the live backend
```

## The 90 seconds

**0–15s — the problem.** A dispatcher is doing two jobs at once: talking to
someone in trouble, and typing the same eight fields. When the caller does not
share their language, there is a third.

**15–40s — a call comes in.** Click **Traffic collision (ES)**. Both
transcripts appear: what the caller actually said, and the English. Point at
the language chip — Whisper identified Spanish at 98%.

**40–65s — the form fills itself.** The fields are already populated:
`traffic_collision`, the address, the callback number, four people involved.
Hover any row and the span it came from lights up in the transcript. Nothing
here is a free-floating assertion.

Then point at `injuries`. It says **unknown**, flagged *disagreement*, with
"another check said confirmed" underneath. That is the system declining to
guess, because two independent reads of the same sentence did not match.

**65–80s — the human decides.** Change `incident_type` and press **confirm**.
The pipeline re-runs; the badge reads *corrected* and the model's own answer is
demoted to a note. Press an approved reply to hear Piper speak it in Spanish.

**80–90s — the numbers.** The latency panel: ~1.1s end to end on this call, of
which about 100ms is the decision engine. Say the honest version of the
accuracy claim — see below.

## The scenarios, and what each is for

| Button | Shows |
| --- | --- |
| Kitchen fire (EN) | The happy path, and a garbled callback number correctly refused |
| Gas smell, no address (EN) | `request_missing_critical_field` with the question to ask; `injuries: none` with the quote "Nobody is hurt." |
| Traffic collision (ES) | Multilingual end to end, cross-check disagreement on `injuries` |
| Intruder with a knife (ES) | Escalation, and the deterministic extractor overruling two disagreeing model passes on `weapons_present` |
| Barely anything said (EN) | Abstention — fields fall to `unknown` because nothing in the transcript supports them |

## Say this, not that

**Do say:** every field cites the words it came from; `unknown` is a real
answer and is distinct from "no"; a value a human confirms is never
overwritten; nothing is dispatched, and the action enum contains no value that
could dispatch anything.

**Do not say** the confidence numbers are calibrated. They are not, and Laya's
own documentation says so — the checkpoints are over-confident and ship
temperatures flagged as invalid for some option counts. The scores rank which
fields need attention. That is all they do, and it is enough.

**Do not claim** real-world time savings. `scripts/timing_study.py` produces a
controlled comparison on synthetic calls with a handful of operators. Describe
it exactly that way.

## A failure worth showing

The most honest moment in this demo is a bug the system caught.

On the Spanish kitchen-fire text, Laya's multilingual checkpoint returns
`gas_leak` at **0.956**. The English translation of the same sentence returns
`structure_fire` at **0.867**. One of them is confidently wrong, and nothing in
either number says which.

Because the pipeline runs both and cross-checks them against a keyword
extractor, the field comes back as `unknown` at 0.219 with both candidates
shown — instead of telling a dispatcher "gas leak, 96%" about a kitchen fire.
That is the product.

## If something breaks

| Symptom | Do this |
| --- | --- |
| Backend unreachable | Check `/api/health`; the dashboard says so plainly |
| GPU unavailable | It falls back to CPU int8 automatically and `/api/health` reports it. Slower, still works |
| Laya will not load | `/api/health` shows the error; the rule extractor still fills location, phone and persons |
| Piper silent | The reply panel shows the phrase text to read aloud |
| Everything is on fire | `DISPATCHIQ_MOCK=1` serves the committed `fixtures/scenarios/*.json` with no models at all |
