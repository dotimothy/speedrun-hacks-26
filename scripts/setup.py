"""Cross-platform bootstrap and environment check.

    python scripts/setup.py              # report what is and is not ready
    python scripts/setup.py --install    # also install deps, voices, fixtures

Written because the three of us are not on the same machine. The stack runs on
Windows/NVIDIA, macOS/Apple Silicon and Linux, but what it runs *on* differs,
and the differences are worth stating rather than discovering at hour six:

* **Whisper** uses CTranslate2, which has CUDA and CPU backends and no Metal
  one. On a Mac it runs on the CPU with int8. That is the only option there,
  not a misconfiguration.
* **Laya** is PyTorch, so it uses CUDA on NVIDIA and MPS on Apple Silicon.
  The decision stage stays fast on a Mac; speech recognition is what slows.
* **Piper** ships wheels for all three with espeak-ng bundled, so there is no
  separate phonemiser to install anywhere.
"""

from __future__ import annotations

import argparse
import importlib
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VOICES = ("en_US-amy-medium", "es_MX-ald-medium")

OK, WARN, BAD = "  ok  ", " warn ", " MISS "
problems: list[str] = []
advice: list[str] = []


def say(state: str, label: str, detail: str = "") -> None:
    print(f"[{state}] {label}" + (f"  {detail}" if detail else ""))


def is_mac() -> bool:
    return platform.system() == "Darwin"


def apple_silicon() -> bool:
    return is_mac() and platform.machine() in ("arm64", "aarch64")


def brew_or(pkg: str, win: str) -> str:
    if is_mac():
        return f"brew install {pkg}"
    if platform.system() == "Linux":
        return f"sudo apt install {pkg}"
    return win


# ---------------------------------------------------------------- checks


def check_python() -> None:
    version = sys.version_info
    ok = version >= (3, 10)
    say(OK if ok else BAD, f"python {platform.python_version()}", "" if ok else "need 3.10+")
    if not ok:
        problems.append("python is older than 3.10 (laya requires 3.10+)")

    # The command to type differs per platform and it trips people up.
    if platform.system() == "Windows":
        advice.append("On Windows use `python`. `python3` is a Microsoft Store alias and will not work.")
    else:
        advice.append("On macOS and Linux use `python3` (and `pip3`); plain `python` often does not exist.")


def check_package(name: str, label: str | None = None, hint: str = "") -> bool:
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "")
        say(OK, label or name, str(version))
        return True
    except Exception:
        say(BAD, label or name, hint or "pip install -r backend/requirements.txt")
        problems.append(f"{label or name} is not importable")
        return False


def check_tool(name: str, install: str, required: bool) -> None:
    found = shutil.which(name)
    if found:
        say(OK, name, found)
    else:
        say(BAD if required else WARN, name, install)
        if required:
            problems.append(f"{name} is not on PATH")


def check_accelerator() -> None:
    try:
        import torch
    except Exception:
        say(BAD, "torch", "pip install -r backend/requirements.txt")
        problems.append("torch missing")
        return

    if torch.cuda.is_available():
        say(OK, "laya device", f"cuda — {torch.cuda.get_device_name(0)}")
    elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        say(OK, "laya device", "mps (Apple Silicon GPU)")
    else:
        say(WARN, "laya device", "cpu — workable, a few hundred ms per call")

    try:
        import ctranslate2

        cuda = set(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        cuda = set()

    if "float16" in cuda:
        say(OK, "whisper device", "cuda / float16")
    elif apple_silicon():
        say(
            OK,
            "whisper device",
            "cpu / int8 — CTranslate2 has no Metal backend, so this is expected on a Mac",
        )
    else:
        say(WARN, "whisper device", "cpu / int8 — slower, still correct")


def check_assets() -> None:
    missing_voices = [v for v in VOICES if not (REPO / "voices" / f"{v}.onnx").exists()]
    if missing_voices:
        say(WARN, "piper voices", "missing " + ", ".join(missing_voices))
        advice.append(
            "Download voices: python -m piper.download_voices "
            + " ".join(VOICES)
            + " --data-dir voices"
        )
    else:
        say(OK, "piper voices", f"{len(VOICES)} present")

    audio = list((REPO / "fixtures" / "audio").glob("*.wav")) if (REPO / "fixtures" / "audio").exists() else []
    if audio:
        say(OK, "fixture audio", f"{len(audio)} files")
    else:
        say(WARN, "fixture audio", "run python scripts/make_fixtures.py")
        advice.append("Generate the demo audio: python scripts/make_fixtures.py")

    if (REPO / "frontend" / "node_modules").exists():
        say(OK, "frontend deps", "node_modules present")
    else:
        say(WARN, "frontend deps", "cd frontend && npm install")


# ---------------------------------------------------------------- install


def run(command: list[str], label: str) -> bool:
    print(f"\n$ {' '.join(command)}")
    result = subprocess.run(command, cwd=REPO)
    if result.returncode != 0:
        print(f"! {label} failed")
        return False
    return True


def do_install() -> int:
    steps_failed = 0
    if not run(
        [sys.executable, "-m", "pip", "install", "-r", "backend/requirements.txt"],
        "pip install",
    ):
        steps_failed += 1

    if not run(
        [sys.executable, "-m", "piper.download_voices", *VOICES, "--data-dir", "voices"],
        "voice download",
    ):
        steps_failed += 1

    if not run([sys.executable, "scripts/make_fixtures.py"], "fixture generation"):
        steps_failed += 1

    npm = shutil.which("npm")
    if npm:
        print(f"\n$ npm install  (in frontend/)")
        if subprocess.run([npm, "install"], cwd=REPO / "frontend").returncode != 0:
            steps_failed += 1
    else:
        print("\n! npm not found; skipping frontend install")
        steps_failed += 1
    return steps_failed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true", help="install deps, voices and fixtures")
    args = parser.parse_args()

    print(f"DispatchIQ setup — {platform.system()} {platform.machine()}\n")

    if args.install:
        failed = do_install()
        print("\n" + "=" * 64)
        if failed:
            print(f"{failed} install step(s) failed; the check below says what is still missing.\n")

    print("\n== environment ==")
    check_python()
    check_tool("ffmpeg", brew_or("ffmpeg", "winget install Gyan.FFmpeg"), required=True)
    check_tool("node", brew_or("node", "winget install OpenJS.NodeJS.LTS"), required=True)
    check_tool("npm", brew_or("node", "winget install OpenJS.NodeJS.LTS"), required=True)

    print("\n== python packages ==")
    for module, label in (
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"),
        ("faster_whisper", "faster-whisper"),
        ("ctranslate2", "ctranslate2"),
        ("laya", "laya"),
        ("piper", "piper-tts"),
    ):
        check_package(module, label)

    print("\n== hardware ==")
    check_accelerator()

    print("\n== assets ==")
    check_assets()

    print("\n" + "=" * 64)
    if problems:
        print(f"{len(problems)} thing(s) need fixing:")
        for problem in problems:
            print("  - " + problem)
        print("\n  python scripts/setup.py --install")
    else:
        print("Ready. Start it with:")
        print("  python -m uvicorn app.main:app --app-dir backend --port 8000")
        print("  cd frontend && npm run dev")

    if advice:
        print("\nNotes for this machine:")
        for note in dict.fromkeys(advice):
            print("  * " + note)

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
