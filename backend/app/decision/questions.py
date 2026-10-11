"""The DispatchIQ question set, in Laya's typed-question format.

Laya answers three question types: ``choice`` (pick one label), ``score``
(ordinal level) and ``noul`` (probability that a statement is true). The field
set from ``schemas.py`` maps onto them like this:

* categorical fields  -> ``choice``, with one criterion line per label
* yes/no/unknown      -> ``noul``, thresholded into a tri-state
* each hazard         -> its own ``noul``
* free text and digits-> not asked of Laya at all; ``rules.py`` extracts those
  deterministically, because a regex is both faster and right more often than
  a model at finding a phone number.

Note what is NOT here: no question asks how urgent the call is, how many units
to send, or what the dispatcher should do. Those are human judgments and the
model is never asked to make them.

``unknown`` is deliberately absent from the ``choice`` criteria. Laya must pick
among real options; ``unknown`` is produced downstream by the abstention
threshold in ``confidence.py``, so it means "the model was not sure enough"
rather than "the model picked a vague label".
"""

from __future__ import annotations

from ..schemas import Hazard

STATE_FIELD = "transcript"

INCIDENT_QUESTIONS: dict[str, dict] = {
    "incident_type": {
        "type": "choice",
        "instructions": f"What kind of emergency does the caller in `{STATE_FIELD}` describe?",
        "criteria": {
            "structure_fire": "a building, room or its contents is burning, or filling with smoke",
            "medical": "illness, injury or a medical emergency, with no fire or crime",
            "traffic_collision": "a crash involving one or more vehicles",
            "crime_in_progress": "a crime is happening now or has just happened",
            "gas_leak": "a smell of gas or a suspected gas leak, with nothing yet burning",
            "water_rescue": "someone is in danger in or under water",
            "other": "a real emergency that none of the other options fits",
        },
    },
    "injuries": {
        "type": "choice",
        "instructions": f"Does `{STATE_FIELD}` say that anyone is hurt?",
        "criteria": {
            "none": "the caller states that nobody is hurt",
            "suspected": "someone may be hurt, or is showing symptoms, but it is not stated outright",
            "confirmed": "the caller states that someone is definitely hurt",
        },
    },
    "weapons_present": {
        "type": "noul",
        "instructions": f"Does `{STATE_FIELD}` indicate a weapon at the scene?",
    },
    # Caller safety is asked as TWO questions, both of which require the
    # transcript to assert something.
    #
    # The obvious single question -- "is the caller safe?" -- was tried first
    # and had to be replaced. It asks a model to confirm the *absence* of
    # danger, and on an emergency call the answer is almost always "no":
    # four of the five fixture scenarios escalated, including a caller who had
    # already said they got out of the building. An escalation signal that
    # fires on everything carries no information.
    #
    # Asking instead for evidence of being trapped, and separately for
    # evidence of having got clear, means "unknown" is returned when the call
    # simply never covered it -- which is the truth, and is what keeps
    # escalation meaningful.
    "caller_trapped": {
        "type": "noul",
        "instructions": (
            f"Does the caller in `{STATE_FIELD}` say they are trapped, hiding, stuck, or "
            "unable to get away from the danger?"
        ),
    },
    "caller_evacuated": {
        "type": "noul",
        "instructions": (
            f"Does the caller in `{STATE_FIELD}` say they have got out, or are outside or "
            "otherwise away from the danger?"
        ),
    },
}

# One noul per hazard. Asking seven small questions in the same forward pass
# costs nothing measurable and gives a per-hazard probability, where a single
# multi-label question would collapse them into one number.
HAZARD_QUESTIONS: dict[str, dict] = {
    f"hazard_{h.value}": {
        "type": "noul",
        "instructions": f"Does `{STATE_FIELD}` indicate {desc}?",
    }
    for h, desc in [
        (Hazard.SMOKE, "smoke"),
        (Hazard.FIRE, "active flames or something burning"),
        (Hazard.GAS, "a gas smell or gas leak"),
        (Hazard.WEAPON, "a weapon"),
        (Hazard.WATER, "flooding or someone in water"),
        (Hazard.ELECTRICAL, "an electrical hazard such as live wires"),
        (Hazard.VEHICLE, "a vehicle involved in the emergency"),
    ]
}

ALL_QUESTIONS: dict[str, dict] = {**INCIDENT_QUESTIONS, **HAZARD_QUESTIONS}

# Terms used only to locate a display span in the transcript for a field Laya
# answered. Laya makes no claim about where in the text its answer came from,
# so there is nothing to falsify here -- a miss means "no span to highlight",
# never "the answer is wrong". Deliberately bilingual for the demo scenarios.
EVIDENCE_TERMS: dict[str, tuple[str, ...]] = {
    "incident_type": (
        "smoke", "fire", "burning", "flames", "humo", "fuego", "incendio",
        "crash", "collision", "accident", "choque", "accidente",
        "gas", "leak", "fuga", "smell", "olor",
        "drowning", "water", "agua", "ahogando",
        "knife", "gun", "weapon", "broke in", "broke into", "intruder", "robbery",
        "cuchillo", "arma", "pistola", "entro a mi casa", "ladron", "robo",
    ),
    "injuries": (
        "hurt", "injured", "bleeding", "coughing", "breathing", "unconscious", "pain",
        "herido", "sangrando", "tosiendo", "respirar", "inconsciente", "dolor",
    ),
    "weapons_present": ("gun", "knife", "weapon", "shot", "arma", "pistola", "cuchillo"),
    "caller_trapped": ("trapped", "stuck", "hiding", "can't get out", "cannot get out",
                       "locked in", "atrapado", "escondido", "no puedo salir", "encerrado"),
    "caller_evacuated": ("outside", "got out", "out of the house", "on the street",
                         "in the hallway", "afuera", "fuera", "sali", "en la calle",
                         "en la banqueta"),
    "hazard_smoke": ("smoke", "humo"),
    "hazard_fire": ("fire", "flames", "burning", "fuego", "incendio", "llamas"),
    "hazard_gas": ("gas", "leak", "fuga", "smell", "olor"),
    "hazard_weapon": ("gun", "knife", "weapon", "arma", "pistola", "cuchillo"),
    "hazard_water": ("water", "flood", "drowning", "agua", "inundacion", "ahogando"),
    "hazard_electrical": ("wire", "electrical", "sparks", "cable", "electrico", "chispas"),
    "hazard_vehicle": ("car", "truck", "vehicle", "motorcycle", "coche", "carro", "camion", "vehiculo"),
}
