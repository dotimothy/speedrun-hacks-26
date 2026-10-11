# Running the demo

## Start it

```bash
python scripts/serve.py
```

Both servers, HTTPS, every interface. It prints the LAN address so you can
open the console on a phone. Each device warns once about the self-signed
certificate — accept it, or the microphone and the API both fail.

Check `/api/health` first: it should report a real device and
`laya.ready: true`.

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

## Live microphone and playback

Two things sit above the results, side by side.

**Call audio** plays whatever the dashboard is currently showing — a fixture
or something you just recorded — with a scrubber and a *save wav* link. Use it
for the 15–40s beat so the room can actually hear the call rather than taking
the transcript on trust. It is also the quickest way to tell a bad
transcription from bad audio: play it back and listen.

**Live microphone** records you, and with *update while speaking* ticked (the
default) it re-transcribes the call every five seconds, so the form fills in
while you are still talking. Watching the fields appear mid-sentence is a
better demonstration of "live" than any still screenshot.

A few things worth knowing before you rely on it on stage:

- Use `localhost`. `getUserMedia` needs a secure context and a LAN IP is not
  one; the panel says so if it is refused.
- The first interim pass waits for three seconds of speech. Below that there
  is not enough for Whisper to do anything but hallucinate filler.
- Each pass re-sends the whole call rather than the newest slice, because
  Whisper reads a complete utterance far better than a fragment cut mid-word.
  On the 3090 that is well under a second; on a Mac, where ASR is on the CPU,
  switch the Whisper model to `tiny` or `base` or untick live mode.
- Overlapping passes are skipped rather than queued, so a slow machine
  degrades to fewer updates rather than to a backlog of stale ones.
- The microphone is routed to a silent gain node, not to the speakers, so an
  open laptop mic will not feed back.

If you want the mic beat but do not trust the room's audio, record a short
call before the judges arrive — the result stays on screen.

## Taking a real phone call

The number is **+1 351-777-6040**, and it reaches a documentation assistant
working from synthetic scenarios. It dispatches nothing.

Three things have to be true at once, and only one of them survives between
sessions:

```bash
python -m uvicorn app.main:app --app-dir backend --port 8000   # 1. backend up
python scripts/dial_demo.py                                    # 2. + 3.
```

`dial_demo.py` opens a Cloudflare quick tunnel, repoints Dial's self-hosted
audio socket at it, turns inbound calling on, and prints the number. Ctrl+C
takes the number down and closes the tunnel.

**The tunnel hostname changes every run.** Quick tunnels are random and
ephemeral, so the URL stored in Dial goes stale the moment the previous one
stops. That is the single most likely reason a call does not connect, and
`python scripts/dial_demo.py --status` says so in as many words:

```
dial connects to  wss://pipes-trend-refined-gained.trycloudflare.com/dispatchiq/audio
that url is       NOT reachable (stale tunnel)
```

Check `--status` before trusting anything else: it reports the number, whether
inbound is on, which mode is active, where Dial will connect, and whether that
address answers.

### What happens on the call

Dial does the telephony and streams μ-law 8 kHz audio to
`/dispatchiq/audio/{call_id}`. The signature is checked before the handshake
is accepted, so a socket with no matching `DIAL_SIGNING_SECRET` refuses every
connection rather than taking calls it cannot authenticate. The loop is
full-duplex with barge-in: if the caller talks over a reply, the queued audio
is dropped and Dial is told to stop playing it.

Replies are the approved phrases and nothing else. The same table the
dashboard uses, spoken by Piper.

When the call ends the audio goes through the normal pipeline and the result
is available at `/api/dispatchiq/calls/{call_id}`.

### Afterwards

```bash
python scripts/dial_demo.py --down
```

Leaving a live number and a public tunnel to your laptop running after a demo
is its own hazard, which is why Ctrl+C does it for you and why there is a
separate switch for when it did not.

## The scenarios, and what each is for

| Button | Shows |
| --- | --- |
| Kitchen fire (EN) | The happy path, and a garbled callback number correctly refused |
| Gas smell, no address (EN) | `request_missing_critical_field` with the question to ask; `injuries: none` with the quote "Nobody is hurt." |
| Traffic collision (ES) | Multilingual end to end, cross-check disagreement on `injuries` |
| Intruder with a knife (ES) | Escalation, and the deterministic extractor overruling two disagreeing model passes on `weapons_present` |
| Barely anything said (EN) | Abstention — fields fall to `unknown` because nothing in the transcript supports them |

## Autonomous mode

Off by default, and the default is the honest setting: everything else in this
product rests on nothing reaching the caller without a person.

Switched on, it speaks one of the approved questions by itself when a field is
missing — after a visible four-second countdown with a **Stop** button. The
panel always shows what it is about to say and why.

The thing to demonstrate is when it *refuses*. Run **Intruder with a knife
(ES)** with autonomous mode on. The panel goes quiet and says so:

> Holding. a weapon or a trapped caller was indicated; handing to a human
> instead of speaking — **escalate to human now**.

A machine talking over the worst moment of a call is the failure worth
designing out, and this is the shape of designing it out. The rules, all
tested: approved phrases only, never during an escalation, never the same
question twice, never a promise that help is coming, and it cannot touch the
form or clear `requires_review`.

For the contrast, run **Gas smell, no address (EN)**: there it asks
*"Can you repeat the street address for me?"* — the question the dispatcher
was about to ask anyway.

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
## Detailed scenario walkthrough

In **Transcript** mode, start a new session and use **Detailed demo** to choose
one of six incident types or one of the four **Provided call:** scripts from
`demo-calls.html`. **Load statement**
fills the editor; **Send utterance** runs that statement through the configured
engine. Review suggestions, then load the next statement. Use a separate session
for each scenario so facts from different calls are never mixed.

The examples include onset, changing conditions, access information, uncertain
people counts, and later corrections. The **Suggested follow-up questions** list
adapts to the incident type and skips accepted values and pending answers. A
conflicting incident classification returns to general clarification questions.
Phone calls use the same question plan, with the existing language, repeat, and
safety-flag controls. Fixed English and Spanish questions cover fire, medical,
collision, crime, gas, and water scenarios; these are documentation prompts,
not treatment or dispatch protocols.

Accept a detail to include it in **Detailed incident report · draft**. Reports
group incident/contact information, people, scenario details, and hazards; they
also retain record revisions, unresolved items, and review flags. Pending
suggestions never become accepted facts or enter the generated report. Sharing
still requires an operator action, and earlier shared versions remain unchanged.

Detailed notes are matched from explicit phrases and preserve the caller's
wording, including negation and uncertainty. They can miss paraphrases or short
answers; use the existing manual edit control when that happens. New phrases
are synthesized on first use by Piper; `tts.prerender_all()` can warm the cache.

### Dedicated model decisions

| Scenario | Model interpretation | Example follow-up |
| --- | --- | --- |
| Fire | Extent, building type, occupant accountability | Where were the unaccounted occupants last seen? |
| Medical | Complaint, awake/breathing, symptom course | What changed, and when? |
| Collision | Vehicle entrapment and road obstruction | Which vehicle, or which lanes? |
| Crime | Person still present or departed; activity ongoing or ended | Current whereabouts or direction of departure |
| Gas | Indoor/outdoor location and reported symptoms | Symptoms described and people affected |
| Water | Visibility and water movement | Last sighting or a visible landmark |

These are typed choices in the shared model question batch. Scenario scope,
confidence checks, and located context determine which suggestions can be
offered; the operator reviews them. The scenario panel labels pending,
accepted, conflicting, and unknown values. A typed decision changes the next
question, while quoted observations retain symptoms, hazards, and location
clues that do not fit a categorical field. Observations accumulate across
utterances and retain revision history. They do not assert a diagnosis or
derive a headcount from a list of relatives.

### Replaying the supplied calls

The caller and dispatcher turns from the provided local HTML are saved in
`backend/evals/demo_call_cases.json`. Run:

```powershell
python scripts/replay_demo_calls.py --output backend/evals/demo_call_results.json
```

This runs local Laya through the v2 transcript API in isolated test sessions,
including explicit scripted review and publication to the local receiving
preview. It makes no phone call. The four calls passed 49 checks for contact
fields, observations, address progression, and exclusion of dispatcher speech.
Full suggestions, follow-ups, final records, and draft reports are in the
output file.

One script, **She gives the wrong house**, needs an operator to confirm the
medical incident type when Laya abstains; the replay records that intervention
under `reviewer_confirmations`. Confirmed incident type now keeps the relevant
questions and fields available even when later model classification is unsure.
The unaccounted neighbor is preserved in the caller's words; a missing model
decision stays unknown and prompts clarification. These results do not measure
audio transcription or live telephone behavior.

The reusable six-scenario diagnostic is separate:

```powershell
python scripts/eval_scenario_decisions.py --output backend/evals/scenario_laya_results.json
```

Its results include model answers, post-policy values, and whether a conditional
follow-up appeared. Low confidence can leave the broader clarification question
active. This small synthetic diagnostic is not a calibrated accuracy benchmark.
