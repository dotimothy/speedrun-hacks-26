"""Record each fixture's real response as the committed expectation.

These files do three jobs at once:

* the dashboard is built against them before the backend is running
  (``DISPATCHIQ_MOCK=1`` serves them straight back),
* they are the demo fallback if the GPU path fails live,
* and ``smoke.py`` asserts the contract against them.

They are *recorded*, not hand-written, so they cannot drift away from what the
pipeline actually returns. Re-run this after any change to the models,
thresholds or question set, and read the diff -- a change you did not expect in
here is a regression.

Usage:
    python scripts/make_expected.py                  # all fixtures, audio path
    python scripts/make_expected.py --transcript     # skip ASR, use the scripts
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"
API = "http://127.0.0.1:8000/api/process"
BOUNDARY = "----dispatchiqfixture"


def post_audio(session_id: str, wav: Path, whisper_size: str) -> dict:
    payload = json.dumps({"session_id": session_id, "whisper_size": whisper_size})
    parts: list[bytes] = []
    parts.append(f"--{BOUNDARY}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="payload"\r\n\r\n')
    parts.append(payload.encode() + b"\r\n")
    parts.append(f"--{BOUNDARY}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="audio"; filename="{wav.name}"\r\n'.encode()
    )
    parts.append(b"Content-Type: audio/wav\r\n\r\n")
    parts.append(wav.read_bytes() + b"\r\n")
    parts.append(f"--{BOUNDARY}--\r\n".encode())
    body = b"".join(parts)

    request = urllib.request.Request(
        API,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.loads(response.read())


def post_transcript(session_id: str, transcript: str) -> dict:
    body = json.dumps({"session_id": session_id, "transcript": transcript}).encode()
    request = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", action="store_true", help="use transcripts, not audio")
    parser.add_argument("--whisper-size", default="small")
    args = parser.parse_args()

    index = json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))
    out_dir = FIXTURES / "scenarios"
    out_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for scenario in index:
        sid = scenario["id"]
        wav = FIXTURES / "audio" / f"{sid}.wav"
        script = (FIXTURES / "transcripts" / f"{sid}.txt").read_text(encoding="utf-8")

        started = time.perf_counter()
        try:
            if args.transcript or not wav.exists():
                data = post_transcript(sid, script)
                mode = "transcript"
            else:
                data = post_audio(sid, wav, args.whisper_size)
                mode = "audio"
        except urllib.error.URLError as exc:
            print(f"! {sid}: {exc}")
            failures += 1
            continue
        elapsed = (time.perf_counter() - started) * 1000

        # Keep the scenario's intent alongside the recorded response so the
        # file explains why it exists.
        data["_fixture"] = {
            "exercises": scenario["exercises"],
            "language_expected": scenario["language"],
            "input_mode": mode,
            "script": script,
        }
        (out_dir / f"{sid}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        action = data["suggested_action"]
        incident = next(
            (p for p in data["predictions"] if p["label"] == "incident_type"), {}
        )
        print(
            f"- {sid:26} {mode:10} {elapsed:7.0f}ms  {action:32} "
            f"incident={incident.get('value')}@{incident.get('score')}"
        )

    print(f"\nwrote {len(index) - failures} expectation files to {out_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
