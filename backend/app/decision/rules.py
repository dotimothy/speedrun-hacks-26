"""Deterministic extraction. No model, no probability, no guessing.

This module does two jobs.

**It owns the fields a regex is simply better at.** A phone number and a street
address are patterns, not judgments. Asking a neural model to find them trades
a correct answer for a plausible one, so Laya is never asked about them -- this
module extracts them and the result is exact or absent.

**It is the second opinion on the fields Laya does own.** Keyword corroboration
is weak evidence on its own, but "two unrelated methods reached the same
answer" is a much better signal than one model's probability. Where this module
and Laya disagree, ``confidence.py`` penalises the field and says so.

Nothing here is tuned to the demo fixtures: the patterns are general and the
keyword sets are bilingual because the product claim is multilingual.
"""

from __future__ import annotations

import re
import time

from .base import EngineResult, FieldAnswer

# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

# Deliberately permissive about separators and strict about length: a 7-to-15
# digit run is a phone number, a 4-digit one is a year or a street number.
_PHONE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3}[\s.-]?\d{3,4}(?:[\s.-]?\d{2,4})?(?!\d)"
)

_STREET_WORDS = (
    r"street|st\.?|avenue|ave\.?|road|rd\.?|boulevard|blvd\.?|drive|dr\.?|lane|ln\.?|"
    r"way|court|ct\.?|place|pl\.?|highway|hwy\.?|apartment|apt\.?|unit|floor|"
    r"calle|avenida|carrera|callejon|piso|apartamento|numero"
)

# "418 Alameda Street", "Calle Alameda 418", "apt 3B" -- a number adjacent to a
# street word, in either order, which is how addresses appear in both languages.
_ADDRESS = re.compile(
    rf"(?:\b(?:{_STREET_WORDS})\b[\s,]*[\w'-]*[\s,]*\d+[\w-]*"
    rf"|\b\d+[\w-]*[\s,]+(?:[\w'-]+[\s,]+){{0,3}}?(?:{_STREET_WORDS})\b)",
    re.IGNORECASE,
)

# Intersections and named streets.
#
# The original address pattern requires a digit next to a street word, so it
# matches "418 Alameda Street" and nothing else. It does not match "Oak and
# Third" -- there is no number -- nor even "Third Street", because an ordinal
# street name is a word, not a digit. That is most of how callers actually
# give a location, and it is the exact example issue #1 is built around, so it
# needs its own pattern rather than a looser version of the existing one.
_ORDINAL = (
    r"\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|"
    r"nineteenth|twentieth|primera|segunda|tercera|cuarta|quinta"
)
# Case-insensitivity is applied per group, never to the whole pattern. A
# global re.IGNORECASE makes [A-Z] match lowercase too, which silently
# destroys the capitalisation test below -- measured: with the flag on, "is
# coughing and bleeding" was extracted as an intersection.
_ORDINAL_CI = rf"(?i:{_ORDINAL})"
_CAP = r"[A-ZÀ-Ü][\w'à-ÿ-]+"
# A street name: an ordinal, or one or two capitalised words.
_NAME = rf"(?:{_ORDINAL_CI}|{_CAP}(?:\s+{_CAP})?)"
_SEP = r"(?i:and|&|/|at|y|con)"
_CORNER = r"(?i:corner\s+of|intersection\s+of|esquina\s+(?:de|con)|cruce\s+de|at|on|near)"

# Two forms, because a bare pair of capitalised words is ambiguous -- "John
# and Mary" is not a location. Either an explicit intersection cue precedes
# it, or one side is an ordinal, which is a strong enough signal alone. This
# is what keeps "coughing and bleeding" and "smoke and fire" out: both sides
# there are ordinary lowercase words.
_INTERSECTION = re.compile(
    rf"(?:{_CORNER})\s+({_NAME})\s+{_SEP}\s+({_NAME})"
    rf"|({_ORDINAL_CI}|{_CAP})\s+{_SEP}\s+({_ORDINAL_CI}|{_CAP})"
)

# "Third Street", "5th Avenue" -- an ordinal name plus a street type.
_STREET_WITH_NAME = re.compile(
    rf"\b(?:{_ORDINAL})\s+(?:{_STREET_WORDS})\b", re.IGNORECASE
)

_ORDINAL_ANCHORED = re.compile(rf"^(?:{_ORDINAL})", re.IGNORECASE)


def _looks_like_intersection(match: "re.Match") -> bool:
    """Reject a bare pair where neither side is an ordinal.

    The cued form (groups 1 and 2) is trusted because "at", "corner of" or
    "esquina de" has already said this is a place. The bare form (groups 3 and
    4) is trusted only when an ordinal appears, which is what separates "Oak
    and Third" from "John and Mary".
    """
    if match.group(1) and match.group(2):
        return True
    left, right = match.group(3), match.group(4)
    if not (left and right):
        return False
    return bool(_ORDINAL_ANCHORED.match(left) or _ORDINAL_ANCHORED.match(right))


# ASR and typed input may omit capitals. Require an explicit location cue and
# an ordinal street on one side rather than treating every "x and y" as a place.
_LOWER_NAME = r"[a-zà-ÿ][\w'à-ÿ-]*(?:\s+[a-zà-ÿ][\w'à-ÿ-]*)?"
_CUED_ORDINAL_INTERSECTION = re.compile(
    rf"\b(?:{_CORNER})\s+((?:{_LOWER_NAME})\s+{_SEP}\s+(?:{_ORDINAL})\b"
    rf"|(?:{_ORDINAL})\s+{_SEP}\s+(?:{_LOWER_NAME}))",
    re.IGNORECASE,
)

def find_intersections(text: str, *, relaxed: bool = False) -> list[str]:
    """Every intersection in the text, in order -- not just the first.

    ``relaxed`` accepts a bare pair of capitalised words with no ordinal and
    no cue -- "Pine and Main". It is off by default because in open text that
    also matches "John and Mary". The caller layer turns it on only when the
    surrounding context has already established that a location is being
    given: an accepted location on file, plus a retraction in this very
    segment. That is narrow enough to be safe and is the difference between
    honouring "actually, Pine and Main" and silently dropping it.
    """
    found: list[str] = []
    for match in _INTERSECTION.finditer(text):
        if not (relaxed or _looks_like_intersection(match)):
            continue
        value = match.group(0).strip(" ,.")
        # Drop a leading cue word so the stored value is the place itself.
        value = re.sub(rf"^{_CORNER}\s+", "", value).strip(" ,.")
        if value and value not in found:
            found.append(value)
    for match in _CUED_ORDINAL_INTERSECTION.finditer(text):
        value = match.group(1).strip(" ,.")
        if value not in found:
            found.append(value)
    return found


def find_all_locations(text: str, *, relaxed: bool = False) -> list[str]:
    """All location candidates, in order of appearance.

    Returning every match rather than the first is what lets the caller layer
    notice that a transcript holds two different locations and raise a
    conflict instead of silently picking one.

    Worth being precise about the failure this avoids, because it is the
    opposite of the obvious one: ``_ADDRESS.search`` returns the **first**
    match, so re-running it over a growing transcript does not mean "the
    newest statement wins" -- it means the oldest one does, forever, even
    after the caller has corrected it.
    """
    found: list[str] = []
    for pattern in (_ADDRESS, _STREET_WITH_NAME):
        for match in pattern.finditer(text):
            value = match.group(0).strip(" ,.")
            if value and value not in found:
                found.append(value)
    for value in find_intersections(text, relaxed=relaxed):
        if value not in found:
            found.append(value)
    return found


def find_all_phones(text: str) -> list[str]:
    found: list[str] = []
    for match in _PHONE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 7 <= len(digits) <= 15:
            value = match.group(0).strip()
            if value not in found:
                found.append(value)
    return found


_PERSONS = re.compile(
    r"\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten"
    r"|un|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    r"(?:people|persons?|passengers?|victims?|kids?|children|adults?|employees?"
    r"|personas?|pasajeros?|victimas?|ninos?|adultos?|empleados?)\b",
    re.IGNORECASE,
)

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "un": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}

# Keyword corroboration for the categorical fields. Order matters only in that
# the highest hit count wins; a tie produces no opinion rather than a coin flip.
_INCIDENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "structure_fire": ("fire", "smoke", "burning", "flames", "on fire",
                       "fuego", "humo", "incendio", "llamas", "quemando"),
    "medical": ("chest pain", "not breathing", "unconscious", "bleeding", "seizure",
                "heart attack", "stroke", "overdose", "collapsed",
                "dolor de pecho", "no respira", "inconsciente", "sangrando",
                "convulsion", "infarto", "desmayo"),
    "traffic_collision": ("crash", "collision", "accident", "hit by a car", "rear-ended",
                          "choque", "accidente", "atropellado", "colision"),
    "crime_in_progress": ("robbery", "breaking in", "stabbed", "shot", "assault",
                          "intruder", "stealing",
                          "robo", "asalto", "apunalado", "disparo", "intruso"),
    "gas_leak": ("gas leak", "smell gas", "smells like gas", "gas smell", "propane",
                 "fuga de gas", "huele a gas", "olor a gas"),
    "water_rescue": ("drowning", "in the water", "swept away", "under the ice",
                     "ahogando", "en el agua", "se ahoga"),
}

_HAZARD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "smoke": ("smoke", "humo"),
    "fire": ("fire", "flames", "burning", "fuego", "llamas", "incendio"),
    "gas": ("gas", "propane", "fuga"),
    "weapon": ("gun", "knife", "weapon", "firearm", "arma", "pistola", "cuchillo"),
    "water": ("flood", "water", "drowning", "inundacion", "agua", "ahogando"),
    "electrical": ("live wire", "wires", "electrical", "sparks", "cable", "chispas"),
    "vehicle": ("car", "truck", "van", "motorcycle", "bus",
                "coche", "carro", "camion", "moto", "autobus"),
}


def _best_keyword_match(text: str, table: dict[str, tuple[str, ...]]) -> tuple[str | None, int]:
    low = text.lower()
    scores = {k: sum(low.count(term) for term in terms) for k, terms in table.items()}
    hits = {k: n for k, n in scores.items() if n > 0}
    if not hits:
        return None, 0
    best = max(hits, key=lambda k: hits[k])
    top = hits[best]
    # A tie between two categories is not an opinion.
    if sum(1 for n in hits.values() if n == top) > 1:
        return None, top
    return best, top


def find_phone(text: str) -> str | None:
    for match in _PHONE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 7 <= len(digits) <= 15:
            return match.group(0).strip()
    return None


def find_address(text: str) -> str | None:
    match = _ADDRESS.search(text)
    return match.group(0).strip(" ,.") if match else None


_CALLER_NAME = re.compile(
    r"\b(?:my name is|me llamo)\s+([A-Za-z]+(?:\s+[A-Za-z]+){0,2})",
    re.IGNORECASE,
)


def find_name(text: str) -> str | None:
    match = _CALLER_NAME.search(text)
    if not match:
        return None
    return " ".join(part.capitalize() for part in match.group(1).split())


def find_extent(text: str) -> str | None:
    low = text.lower()
    if any(phrase in low for phrase in ("spreading", "spread to", "se extiende")):
        return "spreading"
    if any(phrase in low for phrase in ("whole building", "entire building", "todo el edificio")):
        return "whole_building"
    if any(phrase in low for phrase in ("one floor", "the floor", "upstairs", "piso")):
        return "one_floor"
    if any(phrase in low for phrase in ("kitchen", "one room", "the room", "departamento", "apartment")):
        return "one_room"
    if "smoke" in low or "humo" in low:
        if not any(phrase in low for phrase in ("flame", "flames", "fuego", "burning")):
            return "smoke_only"
    return None


def find_occupancy(text: str) -> str | None:
    low = text.lower()
    scores = {
        "multi_unit": sum(phrase in low for phrase in ("apartment", "departamento", "flat")),
        "commercial": sum(phrase in low for phrase in ("restaurant", "store", "office", "employees")),
        "dwelling": sum(phrase in low for phrase in ("house", "home", "casa")),
    }
    hits = {name: count for name, count in scores.items() if count}
    if not hits:
        return None
    best = max(hits, key=lambda name: hits[name])
    if sum(1 for count in hits.values() if count == hits[best]) > 1:
        return None
    return best


def find_complaint(text: str) -> str | None:
    low = text.lower()
    if any(phrase in low for phrase in ("unconscious", "collapsed", "inconsciente", "desmayo")):
        return "unconscious"
    if any(phrase in low for phrase in (
        "not breathing", "isn't breathing", "is not breathing", "no respira",
        "can't breathe", "cannot breathe",
    )):
        return "trouble_breathing"
    if "chest pain" in low or "dolor de pecho" in low:
        return "chest_pain"
    if "bleeding" in low or "sangrando" in low:
        return "bleeding"
    if "seizure" in low or "convulsion" in low:
        return "seizure"
    return None


def find_awake(text: str) -> str | None:
    low = text.lower()
    if any(phrase in low for phrase in ("unconscious", "not awake", "collapsed", "inconsciente")):
        return "no"
    if re.search(r"\bawake\b", low):
        return "yes"
    return None


def find_breathing(text: str) -> str | None:
    low = text.lower()
    if any(phrase in low for phrase in (
        "not breathing", "isn't breathing", "is not breathing", "no respira",
        "can't breathe", "cannot breathe",
    )):
        return "no"
    if re.search(r"\bbreathing\b", low) or "respirando" in low:
        return "yes"
    return None


def find_injuries(text: str) -> str | None:
    low = text.lower()
    if any(phrase in low for phrase in (
        "not breathing", "isn't breathing", "is not breathing", "unconscious", "collapsed",
        "bleeding", "sangrando", "esta herida", "está herida",
    )):
        return "confirmed"
    if any(phrase in low for phrase in ("chest pain", "dolor de pecho", "coughing", "tosiendo")):
        return "suspected"
    if any(phrase in low for phrase in (
        "no one is injured", "nobody is hurt", "not hurt", "nadie esta herido", "nadie está herido",
    )):
        return "none"
    return None


def find_persons(text: str) -> int | None:
    match = _PERSONS.search(text)
    if not match:
        return None
    token = match.group(1).lower()
    if token.isdigit():
        value = int(token)
        return value if 0 < value < 1000 else None
    return _NUMBER_WORDS.get(token)


class RuleEngine:
    """Usable both as the cross-check and as a standalone fallback engine."""

    name = "rules"
    # Runs on this machine.
    is_local = True

    def ready(self) -> bool:
        return True

    def describe(self) -> dict:
        return {
            "name": self.name,
            "model": "regex+keywords",
            "is_local": True,
            "inference_location": "this machine",
        }

    def extract(self, original: str, english: str, language: str | None) -> EngineResult:
        started = time.perf_counter()
        # Patterns are matched against both renderings: a phone number survives
        # translation, but a street name may only be recognisable in one.
        haystack = original if original else english
        joined = "\n".join(t for t in (original, english) if t)

        answers: dict[str, FieldAnswer] = {}

        # All candidates, not the first: the caller layer needs to see when a
        # transcript holds two different locations rather than silently
        # getting whichever one appeared earliest.
        phone_candidates = find_all_phones(joined)
        location_candidates = find_all_locations(haystack) or find_all_locations(joined)

        phone = phone_candidates[0] if phone_candidates else None
        if phone:
            answers["callback_number"] = FieldAnswer(
                label="callback_number", value=phone, quote=phone, decided_on="rules"
            )

        address = location_candidates[0] if location_candidates else None
        if address:
            answers["location_text"] = FieldAnswer(
                label="location_text", value=address, quote=address, decided_on="rules",
                notes=(
                    [f"{len(location_candidates)} location candidates: "
                     + "; ".join(location_candidates)]
                    if len(location_candidates) > 1 else []
                ),
            )

        persons = find_persons(joined)
        if persons is not None:
            answers["persons_involved"] = FieldAnswer(
                label="persons_involved", value=persons, decided_on="rules"
            )

        name = find_name(joined)
        if name:
            answers["caller_name"] = FieldAnswer(
                label="caller_name", value=name, quote=name, decided_on="rules"
            )

        extent = find_extent(joined)
        if extent:
            answers["fire_extent"] = FieldAnswer(
                label="fire_extent", value=extent, decided_on="rules"
            )
        occupancy = find_occupancy(joined)
        if occupancy:
            answers["occupancy"] = FieldAnswer(
                label="occupancy", value=occupancy, decided_on="rules"
            )
        complaint = find_complaint(joined)
        if complaint:
            answers["chief_complaint"] = FieldAnswer(
                label="chief_complaint", value=complaint, decided_on="rules"
            )
        awake = find_awake(joined)
        if awake:
            answers["awake"] = FieldAnswer(label="awake", value=awake, decided_on="rules")
        breathing = find_breathing(joined)
        if breathing:
            answers["breathing"] = FieldAnswer(
                label="breathing", value=breathing, decided_on="rules"
            )
        injuries = find_injuries(joined)
        if injuries:
            answers["injuries"] = FieldAnswer(
                label="injuries", value=injuries, decided_on="rules"
            )

        incident, hits = _best_keyword_match(joined, _INCIDENT_KEYWORDS)
        if incident:
            answers["incident_type"] = FieldAnswer(
                label="incident_type",
                value=incident,
                decided_on="rules",
                notes=[f"{hits} keyword hit(s)"],
            )

        for hazard, terms in _HAZARD_KEYWORDS.items():
            low = joined.lower()
            hit = next((t for t in terms if t in low), None)
            if hit:
                answers[f"hazard_{hazard}"] = FieldAnswer(
                    label=f"hazard_{hazard}", value=True, quote=hit, decided_on="rules"
                )

        return EngineResult(
            answers=answers,
            engine=self.name,
            model="regex+keywords",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            notes=["Deterministic extraction: exact patterns, no inference."],
        )
