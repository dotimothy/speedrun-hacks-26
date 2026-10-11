# Setup

Runs on Windows, macOS (Intel and Apple Silicon) and Linux. Requires Python
**3.10+** (Laya's floor) and Node **18+**.

The fastest path on any of them:

```bash
python scripts/setup.py --install     # macOS/Linux: python3
python scripts/setup.py               # re-run any time to check the environment
```

That installs the Python dependencies, downloads the two Piper voices,
generates the demo audio, and runs `npm install`. The check afterwards prints
what is still missing and the exact command to fix it.

## What differs per platform

The stack runs everywhere, but not on the same hardware:

| | Whisper (CTranslate2) | Laya (PyTorch) |
| --- | --- | --- |
| Windows/Linux + NVIDIA | **CUDA**, float16 | **CUDA** |
| macOS, Apple Silicon | **CPU**, int8 | **MPS** |
| macOS, Intel / any CPU box | **CPU**, int8 | CPU |

The one that surprises people: **CTranslate2 has no Metal backend.**
`get_supported_compute_types("mps")` raises outright, so on a Mac, Whisper
runs on the CPU. That is the only option there, not a misconfiguration, and
`/api/health` reports the resolved device so you can tell at a glance.

Laya *is* PyTorch and does use MPS, so the decision stage stays quick on a
Mac. What slows down is speech recognition, in proportion to clip length —
use `tiny` or `base` from the dashboard's model picker if it drags, or drive
the transcript input, which skips ASR entirely.

## Per platform

### macOS

```bash
brew install ffmpeg node
python3 -m venv .venv && source .venv/bin/activate
python3 scripts/setup.py --install
```

- Use `python3` and `pip3`. Plain `python` often does not exist.
- A virtualenv is worth it here: the system Python is managed by Apple and
  `pip install` into it is a bad time.
- Expect the first run to download ~1.5 GB of model weights.
- Safari is supported for microphone capture (the recorder handles
  `webkitAudioContext` and the suspended-context rule), but Chrome is the
  smoother demo.

### Windows

```powershell
winget install Gyan.FFmpeg OpenJS.NodeJS.LTS
python scripts/setup.py --install
```

- Use `python`. **`python3` is a Microsoft Store alias and will not work.**
- Set `PYTHONUTF8=1` when running scripts that print transcripts; the default
  console codepage is cp1252 and will raise `UnicodeEncodeError` on Spanish
  text.
- GPU inference needs cuDNN 9 alongside CUDA. If it is missing, CTranslate2
  fails at model load and the engine falls back to CPU int8 with a warning in
  the log rather than crashing.

### Linux

```bash
sudo apt install ffmpeg nodejs npm
python3 -m venv .venv && source .venv/bin/activate
python3 scripts/setup.py --install
```

## Running it

```bash
python scripts/serve.py          # HTTPS on 0.0.0.0, both servers
```

That generates a self-signed certificate on first run, starts the backend and
the dashboard, and prints the addresses — including the LAN ones, so you can
open the console on a phone or a second laptop.

`python scripts/serve.py --http` serves plain HTTP on localhost instead, and
`--backend` / `--frontend` start one side only. Or run them by hand:

```bash
python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000   --ssl-certfile certs/dev.crt --ssl-keyfile certs/dev.key
cd frontend && npm run dev       # picks up certs/ automatically
```

The two halves choose TLS independently: the dashboard from whether `certs/`
exists, the backend from whether you passed `--ssl-certfile`. Starting the
backend without those flags while `certs/` happens to exist used to leave the
dev server proxying HTTPS at an HTTP backend, and every `/api` call failed
with nothing on screen explaining why. The dev server now probes the backend
at startup and proxies whichever protocol it actually speaks, printing a line
when the two disagree:

```
[dispatchiq] backend speaks http, not https — proxying http.
```

That mismatch is survivable rather than silent, but `scripts/serve.py` keeps
both sides on the same protocol and is still the easier path.

### Why HTTPS

Not decoration. `getUserMedia` only runs in a secure context: `localhost`
counts as one, a LAN address does not. Over plain HTTP the microphone works
on the demo laptop and silently fails on every other device — which is the
sort of thing you find out five minutes before presenting. Binding `0.0.0.0`
without TLS would be worse than useless: reachable from the network, still
unable to record.

The certificate is signed by nobody, so each device warns once. Accept it, or
both the microphone and the API calls will fail. Regenerate it for a new
network with `python scripts/make_cert.py --force`, or add an address with
`--add 10.0.0.5`.

`certs/` is gitignored: the private key sits there unencrypted, and nothing
in it should ever be reused outside a local demo.

Open `http://127.0.0.1:8000/api/health` first. It reports the resolved Whisper
device and compute type, Laya's device, the platform, and whether the voices
rendered.

Demo from `localhost`. Microphone capture needs a secure context, and a LAN IP
is not one.

## Verifying

```bash
python -m pytest -q          # 67 contract tests, no models or GPU needed
python scripts/smoke.py      # end-to-end against a running backend
python scripts/bench.py      # writes docs/LATENCY.md for your machine
```

The benchmark numbers in `docs/LATENCY.md` are from an RTX 3090. Re-run it on
your own machine before quoting anything.

## Environment variables

All optional; the defaults are what the demo uses.

| Variable | Default | Does |
| --- | --- | --- |
| `DISPATCHIQ_MOCK` | `0` | `1` serves committed fixture responses, no models loaded |
| `DISPATCHIQ_TTS_SPEED` | `0.7` | Piper length scale; lower is faster speech |
| `DISPATCHIQ_SSL_CERT` / `_KEY` | unset | Serve the API over TLS directly |
| `DISPATCHIQ_API` | `http://127.0.0.1:8000` | Where `smoke.py` and `bench.py` look for the backend |
| `DISPATCHIQ_WHISPER_SIZE` | `small` | Default model; the UI can override per request |
| `DISPATCHIQ_WHISPER_CACHE` | `2` | How many Whisper models stay resident |
| `DISPATCHIQ_LAYA_MODEL` | `multilingual` | Default checkpoint |
| `DISPATCHIQ_LAYA_DEVICE` | auto | Force `cuda`, `mps` or `cpu` |
| `DISPATCHIQ_MIN_CHOICE` | `0.55` | Below this a categorical field abstains to `unknown` |
| `DISPATCHIQ_NOUL_HIGH` / `_LOW` | `0.70` / `0.30` | The yes / unknown / no bands |
| `DISPATCHIQ_UNCORROBORATED_CAP` | `0.35` | Cap for a value with no evidence and no second opinion |
| `DISPATCHIQ_CONFLICT_PENALTY` | `0.5` | Multiplier when two passes disagree |

## When it will not start

| Symptom | Cause and fix |
| --- | --- |
| `ModuleNotFoundError: laya` | `pip install -r backend/requirements.txt`; needs Python 3.10+ |
| Whisper loads on CPU on an NVIDIA box | cuDNN 9 missing. `/api/health` shows the resolved device |
| `UnicodeEncodeError` on Windows | `set PYTHONUTF8=1` |
| `npm run dev` rejects the Node version | Vite is pinned to 6.4.4 for Node 20.13; do not bump it to 7 or 8 without Node 20.19+ |
| Microphone button does nothing | Not on `localhost` and not on HTTPS — run `scripts/serve.py`, or permission was denied |
| Browser warns about the certificate | Expected; it is self-signed. Accept once per device |
| API calls fail from a phone but work locally | The certificate was not accepted for the API origin. Open `https://<lan-ip>:8000/api/health` once and accept it too |
| Every `/api` call 500s in the dashboard | Protocol mismatch between the dev server and the backend. The startup line says which the backend speaks; `scripts/serve.py` avoids it |
| Nothing works and the demo is in five minutes | `DISPATCHIQ_MOCK=1` — the committed fixtures are served with no models at all |


### OpenAI Decisions for live sessions

Set these in the ignored repository `.env` (never commit credentials):

```dotenv
OPENAI_API_KEY=your-key
DISPATCHIQ_ALLOW_CLOUD=1
DISPATCHIQ_DECISION_ENGINE=openai_decisions
DISPATCHIQ_DECISIONS_MODEL=gpt-6-luna
```

Restart the API after changing configuration. Inherited environment variables
override `.env`. This adapter uses OpenAI's `/v1/decisions` endpoint for typed
incident and safety questions. Caller transcript text leaves this machine.
Use synthetic calls for the prototype. Addresses and phone numbers still use
deterministic extraction; this is not unrestricted entity extraction. All
results remain proposals requiring human review. API failures retain input for
retry instead of silently substituting rules. Scores are not calibrated for
emergency calls.

The live v2 session path performs real inference even when `DISPATCHIQ_MOCK=1`
is used to skip local model warmup; the older v1 fixture path remains separate.
Decisions does not transcribe audio: microphone input still requires a working
Whisper installation. Use `http://localhost:5173/?input=transcript` to test typed
utterances independently of speech recognition.

API documentation: https://developers.openai.com/api/docs/guides/decisions
