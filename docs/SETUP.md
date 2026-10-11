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
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
cd frontend && npm run dev          # http://localhost:5173
```

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
| Microphone button does nothing | Not on `localhost`, or permission denied |
| Nothing works and the demo is in five minutes | `DISPATCHIQ_MOCK=1` — the committed fixtures are served with no models at all |
