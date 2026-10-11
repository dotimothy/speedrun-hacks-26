"""Turn inbound calls to the Dial number on or off, or report the current state.

While inbound is on, anyone who calls the number reaches the DispatchIQ intake
greeting. Keep it off except while a live demo is running, since nothing here
dispatches anyone.

    python scripts/inbound.py status
    python scripts/inbound.py off
    python scripts/inbound.py on

The API key and number come from the environment, or from a ``.env`` file at
the repo root (``--env`` to point elsewhere). Nothing is printed except the
state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.getdial.ai/api/v1/numbers"
# Cloudflare rejects the default Python user agent, so identify the script.
USER_AGENT = "dispatchiq-inbound/1.0"


def load_env(path: Path) -> None:
    """Read KEY=VALUE lines into the environment without overriding it."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def request(method: str, url: str, key: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def find_number(key: str, number: str) -> dict:
    listing = request("GET", API, key)
    for entry in listing.get("numbers", []):
        if entry.get("number") == number:
            return entry
    raise SystemExit(f"{number} is not on this account")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["status", "on", "off"])
    parser.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    args = parser.parse_args()

    load_env(args.env)
    key = os.environ.get("DIAL_API_KEY", "")
    number = os.environ.get("DIAL_NUMBER", "")
    if not key or not number:
        print("DIAL_API_KEY and DIAL_NUMBER must be set (environment or .env)", file=sys.stderr)
        return 2

    try:
        entry = find_number(key, number)
        if args.action in ("on", "off"):
            enabled = args.action == "on"
            entry = request("PATCH", f"{API}/{entry['id']}", key, {"callingEnabled": enabled})
            entry = find_number(key, number)
        state = "on" if entry["callingEnabled"] else "off"
    except urllib.error.HTTPError as exc:
        print(f"Dial API returned HTTP {exc.code}", file=sys.stderr)
        return 1

    print(f"inbound calls to {number}: {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
