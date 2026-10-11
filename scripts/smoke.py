"""End-to-end check against a running backend.

Asserts the things the product claims, against the live pipeline rather than
against recorded output. Run it before any demo:

    python -m uvicorn app.main:app --app-dir backend --port 8000
    python scripts/smoke.py

Exit code is the number of failed assertions, so it is usable in CI.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"

PASS, FAIL = "  ok  ", " FAIL "
failures: list[str] = []


def check(condition: bool, description: str, detail: str = "") -> None:
    print(("[" + (PASS if condition else FAIL) + "] " + description) + (f"  {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(description)


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=60) as response:
        return json.loads(response.read())


def post_json(path: str, payload: dict, timeout: int = 600) -> dict:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def post_audio(session_id: str, wav: Path, size: str) -> dict:
    boundary = "----dispatchiqsmoke"
    payload = json.dumps({"session_id": session_id, "whisper_size": size})
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="payload"\r\n\r\n',
        payload.encode() + b"\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="audio"; filename="{wav.name}"\r\n'.encode(),
        b"Content-Type: audio/wav\r\n\r\n",
        wav.read_bytes() + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        BASE + "/api/process",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--whisper-size", default="small")
    parser.add_argument("--skip-audio", action="store_true")
    args = parser.parse_args()

    # ---------------------------------------------------------------- health
    print("\n== health ==")
    try:
        health = get("/api/health")
    except urllib.error.URLError as exc:
        print(f"backend not reachable at {BASE}: {exc}")
        return 1
    check(health["status"] == "ok", "backend responds")
    check(health["laya"]["ready"], "laya loaded", str(health["laya"]["error"]))
    check(health["whisper"]["error"] is None, "whisper loadable")
    check(bool(health["calibration_warning"]), "calibration warning is exposed, not hidden")
    print(f"       whisper on {health['whisper']['device']}/{health['whisper']['compute_type']}")

    # ----------------------------------------------------- transcript path
    print("\n== transcript path ==")
    data = post_json("/api/process", {
        "session_id": "smoke-transcript",
        "transcript": "My stove is on fire at 42 Maple Street. Nobody is hurt. Call me on 555 0101.",
    })
    check(data["requires_review"] is True, "requires_review is true")
    by_label = {p["label"]: p for p in data["predictions"]}
    check(by_label["incident_type"]["value"] == "structure_fire", "stove fire reads as structure_fire",
          str(by_label["incident_type"]["value"]))
    check(by_label["injuries"]["value"] == "none", "'nobody is hurt' is none, not unknown",
          str(by_label["injuries"]["value"]))
    check(by_label["callback_number"]["value"] is not None, "phone number extracted")
    check(by_label["location_text"]["value"] is not None, "address extracted")
    check(data["engine"]["asr_confidence"] == "not_applicable",
          "no ASR confidence is invented when there is no audio")

    # --------------------------------------------------- contract per field
    print("\n== contract ==")
    for pred in data["predictions"]:
        ok = 0.0 <= pred["score"] <= 1.0 and "evidence" in pred
        if not ok:
            check(False, f"{pred['label']} carries a score and an explicit evidence key")
    check(True, "every prediction carries a score and an explicit evidence key")

    sources = {"original": data["transcript_original"], "english": data["transcript_english"]}
    bad = [
        p["label"] for p in data["predictions"]
        if p["evidence"] and sources[p["evidence"]["source"]][p["evidence"]["start"]:p["evidence"]["end"]] != p["evidence"]["text"]
    ]
    check(not bad, "evidence offsets slice back to the quoted text", str(bad))

    overconfident = [
        (p["label"], p["score"]) for p in data["predictions"]
        if p["value"] == "unknown" and p["score"] >= 0.55
    ]
    check(not overconfident, "abstentions are not dressed up as findings", str(overconfident))

    # -------------------------------------------- confirmed-field protection
    print("\n== the dispatcher's value wins ==")
    post_json("/api/sessions/smoke-protect/confirm", {"fields": {"incident_type": "gas_leak"}})
    protected = post_json("/api/process", {
        "session_id": "smoke-protect",
        "transcript": "My stove is on fire at 42 Maple Street.",
    })
    field = next(p for p in protected["predictions"] if p["label"] == "incident_type")
    check(field["value"] == "gas_leak", "a confirmed value survives a disagreeing model pass", str(field["value"]))
    check(field["verification_state"] in ("confirmed", "corrected"), "it is marked as human-verified")
    check(field["alternative"] == "structure_fire", "the model's view is preserved, not discarded",
          str(field["alternative"]))
    check(protected["requires_review"] is True, "still requires review once confirmed")

    # ------------------------------------------------------- no dispatch
    print("\n== no automated dispatch ==")
    banned = ("dispatch", "send_unit", "deploy")
    check(not any(word in protected["suggested_action"] for word in banned),
          "suggested_action never dispatches anything")

    # ------------------------------------------------------------- audio
    if not args.skip_audio:
        print("\n== audio path ==")
        index = json.loads((REPO / "fixtures" / "index.json").read_text(encoding="utf-8"))
        for scenario in index:
            sid = scenario["id"]
            wav = REPO / "fixtures" / "audio" / f"{sid}.wav"
            if not wav.exists():
                check(False, f"{sid}: fixture audio present", "run scripts/make_fixtures.py")
                continue
            result = post_audio("smoke-" + sid, wav, args.whisper_size)
            expected_language = scenario["language"]
            check(bool(result["transcript_original"].strip()), f"{sid}: transcribed")
            check((result["detected_language"] or "").startswith(expected_language),
                  f"{sid}: language detected as {expected_language}", str(result["detected_language"]))
            if expected_language != "en":
                check(result["transcript_english"] != result["transcript_original"],
                      f"{sid}: English translation differs from the original")
            check(result["latency_ms"]["server_total"] > 0, f"{sid}: latency measured")
            check(result["requires_review"] is True, f"{sid}: requires review")

    print("\n" + ("-" * 60))
    if failures:
        print(f"{len(failures)} failed:")
        for failure in failures:
            print("  - " + failure)
    else:
        print("all checks passed")
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())
