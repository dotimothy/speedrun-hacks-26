"""Generate the synthetic call audio and transcripts, fully locally.

Everything here is invented. There is no real call audio in this repository and
no recording of a real person, which is a hard constraint of the project rather
than a convenience: the addresses are sample addresses and the phone numbers
are in the 555 reserved range.

The audio is synthesised with Piper -- the same TTS the reply path uses, so
there is no extra dependency -- and then pushed through ffmpeg's phone band
(300-3400 Hz, mild noise) so it resembles a call rather than a studio read.
That matters for more than realism: on clean synthetic audio Whisper reports a
per-word confidence of ~0.99 for everything, and the scoring pipeline then has
no ASR signal to work with. Degraded audio makes those numbers mean something.

Usage:
    python scripts/make_fixtures.py            # audio + transcripts
    python scripts/make_fixtures.py --clean    # regenerate from scratch
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

FIXTURES = REPO / "fixtures"
AUDIO_DIR = FIXTURES / "audio"
TRANSCRIPT_DIR = FIXTURES / "transcripts"
VOICES = REPO / "voices"

VOICE_FOR = {"en": "en_US-amy-medium", "es": "es_MX-ald-medium"}

# Each scenario exercises a different path through the pipeline. The notes are
# the reason the scenario exists, so nobody later "fixes" one into redundancy.
SCENARIOS: list[dict] = [
    {
        "id": "kitchen_fire_en",
        "language": "en",
        "exercises": "happy path: every critical field present, hazards, suspected injury",
        "text": (
            "Help, there is smoke coming from my kitchen and my son is coughing badly. "
            "I am at 418 Alameda Street, apartment 3B. "
            "My number is 555 0147. I got him out into the hallway."
        ),
    },
    {
        "id": "gas_smell_no_address_en",
        "language": "en",
        "exercises": "missing critical field: caller cannot give an address",
        "text": (
            "I can smell gas really strongly in the stairwell. "
            "I don't know the street number, I just moved in, I am sorry. "
            "Nobody is hurt. You can reach me on 555 0192."
        ),
    },
    {
        "id": "traffic_collision_es",
        "language": "es",
        "exercises": "multilingual end to end: Spanish original plus English translation",
        "text": (
            "Hubo un choque entre dos coches en la Avenida Juarez 250. "
            "Hay cuatro personas en el carro y uno esta sangrando. "
            "Mi numero es 555 0163. Estoy afuera, en la banqueta."
        ),
    },
    {
        "id": "weapon_crime_es",
        "language": "es",
        "exercises": "escalation path: weapon indicated, caller not safe",
        "text": (
            "Alguien entro a mi casa y tiene un cuchillo. "
            "Estoy escondido adentro en el bano, no puedo salir. "
            "Calle Morelos 88. Mi numero es 555 0175."
        ),
    },
    {
        "id": "ambiguous_noise_en",
        "language": "en",
        "exercises": "abstention: barely any information, most fields must stay unknown",
        "text": "Hello? Something is wrong. I... I can't really tell. Please.",
    },
    {
        "id": "medical_chest_pain_en",
        "language": "en",
        "exercises": "medical: chest pain, awake, breathing, caller, address, callback",
        "text": (
            "This is a medical emergency. My name is Helen Cho. My father is seventy "
            "and he has chest pain. He is awake and breathing. We are at our house "
            "at 220 Cedar Avenue. Please call me back at 555 0138."
        ),
    },
    {
        "id": "medical_not_breathing_en",
        "language": "en",
        "exercises": "medical: collapsed, not breathing, apartment address",
        "text": (
            "This is a medical emergency. My name is Marcus Hale. My wife collapsed "
            "and she is not breathing. We are at 48 Elm Street, apartment 2. "
            "Please call me back at 555 0144."
        ),
    },
    {
        "id": "commercial_fire_en",
        "language": "en",
        "exercises": "fire: commercial kitchen, one room, everyone outside",
        "text": (
            "There is smoke and flames in the kitchen of the restaurant at 900 Market "
            "Street. My name is Luis Ortega. All six employees are outside. "
            "My callback is 555 0184. No one is injured."
        ),
    },
    {
        "id": "apartment_fire_es",
        "language": "es",
        "exercises": "Spanish apartment fire: one unit, caller already outside",
        "text": (
            "Hay humo y fuego en Calle Reforma 120, departamento 4. "
            "Me llamo Ana Ruiz. Ya sali a la calle. Hay dos personas afuera conmigo. "
            "Mi numero es 555 0126. Nadie esta herido."
        ),
    },
]


def synthesize(text: str, language: str, out_path: Path) -> bool:
    voice = VOICES / f"{VOICE_FOR[language]}.onnx"
    if not voice.exists():
        print(f"  ! missing voice {voice.name}; run: python -m piper.download_voices "
              f"{VOICE_FOR[language]} --data-dir voices")
        return False
    from piper import PiperVoice

    loaded = PiperVoice.load(str(voice))
    with wave.open(str(out_path), "wb") as handle:
        loaded.synthesize_wav(text, handle)
    return True


def phone_band(src: Path, dst: Path) -> bool:
    """Approximate a phone line: band-limited, 16 kHz mono, a little noise."""
    if not shutil.which("ffmpeg"):
        print("  ! ffmpeg not found; keeping clean audio")
        shutil.copyfile(src, dst)
        return False
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-af", "highpass=f=300,lowpass=f=3400,acompressor=threshold=-18dB:ratio=3,volume=0.95",
        "-ar", "16000", "-ac", "1",
        str(dst),
    ]
    return subprocess.run(cmd, check=False).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true", help="delete existing fixtures first")
    args = parser.parse_args()

    if args.clean:
        for directory in (AUDIO_DIR, TRANSCRIPT_DIR):
            shutil.rmtree(directory, ignore_errors=True)

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

    index = []
    for scenario in SCENARIOS:
        sid = scenario["id"]
        print(f"- {sid} ({scenario['language']}): {scenario['exercises']}")

        transcript_path = TRANSCRIPT_DIR / f"{sid}.txt"
        final = AUDIO_DIR / f"{sid}.wav"
        previous = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else None
        transcript_path.write_text(scenario["text"], encoding="utf-8")
        if (
            not args.clean
            and previous == scenario["text"]
            and final.exists()
            and final.stat().st_size > 0
        ):
            print("  transcript unchanged, audio left in place")
            index.append(scenario)
            continue

        raw = AUDIO_DIR / f"{sid}.raw.wav"
        if synthesize(scenario["text"], scenario["language"], raw):
            phone_band(raw, final)
            raw.unlink(missing_ok=True)
            size = final.stat().st_size if final.exists() else 0
            print(f"  wrote {final.name} ({size // 1024} KB)")
        index.append(scenario)

    (FIXTURES / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\n{len(index)} scenarios. Expected responses: python scripts/make_expected.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
