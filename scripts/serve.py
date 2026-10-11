"""Start the backend and the dashboard together, over HTTPS, on every interface.

    python scripts/serve.py              # https on 0.0.0.0, both servers
    python scripts/serve.py --http       # plain http on localhost only
    python scripts/serve.py --backend    # backend only

Two reasons this is not just two shell commands in the README.

**HTTPS is load-bearing, not decoration.** `getUserMedia` refuses to run
outside a secure context. `localhost` is one; `192.168.1.17` is not. Without
TLS the microphone works on the demo laptop and silently fails on every other
device, which is exactly the kind of thing discovered five minutes before
presenting.

**Binding 0.0.0.0 without TLS would be worse than useless** -- reachable from
the network, still unable to use the microphone. So the two go together, and
this script keeps them together.

Stop both with Ctrl+C.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CERT = REPO / "certs" / "dev.crt"
KEY = REPO / "certs" / "dev.key"


def local_addresses() -> list[str]:
    found: set[str] = set()
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        found.add(probe.getsockname()[0])
        probe.close()
    except Exception:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="plain http, localhost only")
    parser.add_argument("--backend", action="store_true", help="backend only")
    parser.add_argument("--frontend", action="store_true", help="dashboard only")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--ui-port", type=int, default=5173)
    args = parser.parse_args()

    secure = not args.http
    if secure and not (CERT.exists() and KEY.exists()):
        print("No certificate found. Generating one...\n")
        if subprocess.run([sys.executable, "scripts/make_cert.py"], cwd=REPO).returncode != 0:
            print("\nCould not create a certificate. Falling back to http on localhost.")
            secure = False
        print()

    scheme = "https" if secure else "http"
    host = "0.0.0.0" if secure else "127.0.0.1"

    processes: list[tuple[str, subprocess.Popen]] = []

    if not args.frontend:
        command = [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--app-dir", "backend",
            "--host", host,
            "--port", str(args.api_port),
        ]
        if secure:
            command += ["--ssl-certfile", str(CERT), "--ssl-keyfile", str(KEY)]

        environment = dict(os.environ)
        # Spanish transcripts through a cp1252 console raise UnicodeEncodeError
        # on Windows; this costs nothing elsewhere.
        environment["PYTHONUTF8"] = "1"
        processes.append(("backend", subprocess.Popen(command, cwd=REPO, env=environment)))

    if not args.backend:
        npm = shutil.which("npm")
        if npm is None:
            print("npm not found; skipping the dashboard")
        else:
            # vite.config.ts picks up the certificate on its own, so the same
            # command serves http or https depending on what exists on disk.
            processes.append((
                "dashboard",
                subprocess.Popen(
                    [npm, "run", "dev", "--", "--port", str(args.ui_port)],
                    cwd=REPO / "frontend",
                    shell=(os.name == "nt"),
                ),
            ))

    time.sleep(2)
    print("\n" + "=" * 62)
    print(f"  dashboard   {scheme}://localhost:{args.ui_port}")
    print(f"  api         {scheme}://localhost:{args.api_port}/api/health")
    if secure:
        for address in local_addresses():
            print(f"  on the LAN  {scheme}://{address}:{args.ui_port}")
        print()
        print("  The certificate is self-signed, so each device warns once.")
        print("  Accept it, or the microphone and the API calls will both fail.")
    else:
        print("\n  Plain HTTP: the microphone works on this machine only.")
    print("=" * 62 + "\n")

    def shutdown(*_: object) -> None:
        for name, process in processes:
            if process.poll() is None:
                print(f"stopping {name}...")
                process.terminate()
        for _, process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    try:
        while True:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    print(f"\n{name} exited with code {code}")
                    shutdown()
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
