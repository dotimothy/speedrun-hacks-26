"""Bring the phone number up for a demo, and take it back down afterwards.

Dial is a cloud service, so it has to reach this machine. A Cloudflare quick
tunnel gives a public ``wss://`` URL for the duration of the demo, this script
points Dial's self-hosted audio mode at it, and turns inbound calling on.

    python scripts/dial_demo.py              # bring up, print the number, wait
    python scripts/dial_demo.py --status     # report without changing anything
    python scripts/dial_demo.py --down       # turn inbound off, nothing else

Ctrl+C takes it back down: inbound off, tunnel closed. That teardown is the
reason this is a script rather than three commands. A quick tunnel is a public
ingress to this laptop and a live number is a phone anyone can ring, and the
usual way those get left running is that a demo ended and nobody remembered.

The tunnel hostname is random and changes on every run, so the Dial config has
to be rewritten each time. That is a property of quick tunnels, not a bug.

Nothing here dispatches anyone. The number reaches a documentation assistant
working from synthetic scenarios.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Line-buffered: this script's whole job is to print an address and a phone
# number, and a block-buffered pipe means neither appears until it exits --
# which it does not, because it waits for Ctrl+C.
sys.stdout.reconfigure(line_buffering=True)

REPO = Path(__file__).resolve().parents[1]
API = "https://api.getdial.ai/api/v1"
USER_AGENT = "dispatchiq-dial-demo/1.0"
BACKEND_PORT = 8000

CLOUDFLARED_CANDIDATES = (
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
)


def load_env() -> None:
    path = REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def api(method: str, path: str, key: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def find_number(key: str, number: str) -> dict:
    for entry in api("GET", "/numbers", key).get("numbers", []):
        if entry.get("number") == number:
            return entry
    raise SystemExit(f"{number} is not on this Dial account")


def set_inbound(key: str, number: str, enabled: bool) -> str:
    entry = find_number(key, number)
    api("PATCH", f"/numbers/{entry['id']}", key, {"callingEnabled": enabled})
    return "on" if enabled else "off"


def cloudflared_path() -> str:
    found = shutil.which("cloudflared")
    if found:
        return found
    for candidate in CLOUDFLARED_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise SystemExit(
        "cloudflared not found. Install it with:\n"
        "  winget install Cloudflare.cloudflared"
    )


def backend_url() -> str | None:
    """Find the backend and the protocol it is actually speaking.

    Not hardcoded, because the backend's TLS depends on whether uvicorn was
    given --ssl-certfile and nothing here can know that. Guessing produced
    exactly one bug already: after the server moved to HTTPS this script
    reported it DOWN and would have pointed the tunnel at the wrong scheme.
    """
    import ssl

    unverified = ssl._create_unverified_context()
    for scheme in ("https", "http"):
        url = f"{scheme}://127.0.0.1:{BACKEND_PORT}"
        try:
            context = unverified if scheme == "https" else None
            with urllib.request.urlopen(url + "/api/health", timeout=5, context=context):
                return url
        except Exception:
            continue
    return None


def backend_is_up() -> bool:
    return backend_url() is not None


def start_tunnel() -> tuple[subprocess.Popen, str]:
    """Open a quick tunnel to the backend and return its public hostname."""
    target = backend_url() or f"http://127.0.0.1:{BACKEND_PORT}"

    # cloudflared verifies the ORIGIN's certificate, and ours is the
    # self-signed one from scripts/make_cert.py. Without this the tunnel
    # comes up, Dial connects to it happily, and every request dies at the
    # last hop -- a call that rings and then hears nothing, with the failure
    # two layers away from anything the dashboard can show.
    command = [cloudflared_path(), "tunnel", "--url", target, "--no-autoupdate"]
    if target.startswith("https://"):
        command.insert(2, "--no-tls-verify")

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    deadline = time.time() + 60
    while time.time() < deadline:
        line = process.stdout.readline() if process.stdout else ""
        if not line and process.poll() is not None:
            raise SystemExit("cloudflared exited before giving a URL")
        match = pattern.search(line or "")
        if match:
            return process, match.group(0)
    process.terminate()
    raise SystemExit("cloudflared did not produce a URL within 60s")


def point_dial_at(key: str, https_url: str, mode: str) -> str:
    """Repoint Dial's self-hosted socket at this run's tunnel.

    ``save`` rather than ``activate``: self-hosted is already enabled on this
    account, and ``activate`` would also route every call through this mode,
    which is a bigger change than a demo should make on its own.
    """
    ws_url = https_url.replace("https://", "wss://") + f"/dispatchiq/{mode}"
    config = {"type": mode, "wsUrl": ws_url}
    if mode == "audio":
        config["audioInboundFormat"] = "mulaw_8000"
        config["audioOutboundFormat"] = "mulaw_8000"
    api("POST", "/self-hosted", key, {"action": "save", "config": config})
    return ws_url


def status(key: str, number: str) -> int:
    entry = find_number(key, number)
    config = api("GET", "/self-hosted", key)
    active = config.get("activeMode", "?")
    ws_url = (config.get(active) or {}).get("wsUrl", "(none)")
    print(f"number            {number}")
    print(f"inbound calling   {'on' if entry.get('callingEnabled') else 'off'}")
    print(f"self-hosted       {'enabled' if config.get('enabled') else 'disabled'}, mode={active}")
    print(f"dial connects to  {ws_url}")
    local = backend_url()
    print(f"backend here      {local if local else 'DOWN — start it first'}")

    reachable = "unknown"
    if ws_url.startswith("wss://"):
        probe = ws_url.replace("wss://", "https://").rsplit("/dispatchiq", 1)[0]
        try:
            with urllib.request.urlopen(probe + "/api/health", timeout=10):
                reachable = "reachable"
        except Exception:
            reachable = "NOT reachable (stale tunnel)"
    print(f"that url is       {reachable}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="report, change nothing")
    parser.add_argument("--down", action="store_true", help="turn inbound off and exit")
    parser.add_argument("--mode", default="audio", choices=["audio", "llm"])
    args = parser.parse_args()

    load_env()
    key = os.environ.get("DIAL_API_KEY", "")
    number = os.environ.get("DIAL_NUMBER", "")
    if not key or not number:
        print("DIAL_API_KEY and DIAL_NUMBER must be set in .env", file=sys.stderr)
        return 2

    if args.status:
        return status(key, number)

    if args.down:
        print(f"inbound calls to {number}: {set_inbound(key, number, False)}")
        return 0

    secret_var = "DIAL_SIGNING_SECRET" if args.mode == "audio" else "DIAL_LLM_SIGNING_SECRET"
    if not os.environ.get(secret_var):
        print(f"{secret_var} is not set; the socket will refuse every call", file=sys.stderr)
        return 2

    if not backend_is_up():
        print(
            "The backend is not answering on 127.0.0.1:8000. Start it first:\n"
            "  python -m uvicorn app.main:app --app-dir backend --port 8000",
            file=sys.stderr,
        )
        return 2

    print("opening a public tunnel to this machine...")
    process, https_url = start_tunnel()
    ws_url = point_dial_at(key, https_url, args.mode)
    state = set_inbound(key, number, True)

    print()
    print("=" * 64)
    print(f"  CALL  {number}")
    print("=" * 64)
    print(f"  tunnel        {https_url} -> {backend_url()}")
    print(f"  dial connects {ws_url}")
    print(f"  inbound       {state}")
    print(f"  mode          {args.mode}")
    print()
    print("  Watch the call land:  http://127.0.0.1:8000/api/health")
    print("  Results per call:     /api/dispatchiq/calls/{call_id}")
    print()
    print("  This number reaches a documentation assistant working from")
    print("  synthetic scenarios. It dispatches nothing and routes nothing.")
    print()
    print("  Ctrl+C to take the number down and close the tunnel.")
    print("=" * 64)

    def shutdown(*_: object) -> None:
        print("\ntaking it down...")
        try:
            print(f"  inbound calls: {set_inbound(key, number, False)}")
        except Exception as exc:
            print(f"  could not disable inbound ({exc}) -- do it in the Dial console")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        print("  tunnel closed")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    try:
        while process.poll() is None:
            time.sleep(1)
        print("cloudflared exited; the number is no longer reachable")
        shutdown()
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
