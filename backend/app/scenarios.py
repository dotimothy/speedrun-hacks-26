"""Scenario-specific documentation prompts, not response or treatment protocols.

Detailed values are verbatim caller statements. We do not turn a description
into a diagnosis, a head count, or a claim that somebody has been accounted for.
"""
from __future__ import annotations

import re
from .decision.scenario_tasks import DECISIONS

BASE_QUESTIONS = (
    ("location_text", "repeat_address"),
    ("incident_type", "what_happening"),
    ("callback_number", "ask_callback"),
    ("injuries", "ask_injuries"),
    ("caller_safe", "ask_safe"),
)

SCENARIO_FIELDS = {
    "structure_fire": ("occupancy", "fire_extent", "fire_observations", "people_accounted_for", "persons_involved"),
    "medical": ("chief_complaint", "awake", "breathing", "symptom_details", "patient_age", "medical_history"),
    "traffic_collision": ("vehicle_details", "persons_involved", "people_accounted_for", "road_conditions"),
    "crime_in_progress": ("suspect_location", "weapons_present", "suspect_description", "persons_involved"),
    "gas_leak": ("gas_source", "occupancy", "people_accounted_for", "persons_involved"),
    "water_rescue": ("last_seen", "persons_involved", "water_conditions", "people_accounted_for"),
}
for _name, _spec in DECISIONS.items():
    SCENARIO_FIELDS[_spec[0]] = (*SCENARIO_FIELDS[_spec[0]], _name)

DETAIL_LABELS = {
    "symptom_details": "Symptoms and changes in caller's words",
    "fire_observations": "Fire and smoke observations",
    "scene_hazards": "Hazard descriptions",
    "location_context": "Location clues and unit descriptions",
    "people_details": "People described by caller",
    "incident_timing": "Onset and sequence",
    "access_notes": "Access and whereabouts",
    "patient_age": "Patient age as reported",
    "medical_history": "Relevant history reported",
    "vehicle_details": "Vehicles described",
    "road_conditions": "Road conditions and obstruction",
    "people_accounted_for": "People's whereabouts",
    "suspect_description": "Person described by caller",
    "suspect_location": "Person's last reported location",
    "gas_source": "Gas smell or source described",
    "water_conditions": "Water conditions described",
    "last_seen": "Last seen location",
    "occupant_last_seen": "Last known location of occupants",
    "symptom_changes": "Description of symptom changes",
    "trapped_vehicle_location": "Vehicle and position of trapped person",
    "affected_lanes": "Affected lanes or direction",
    "departure_direction": "Reported direction of departure",
    "person_whereabouts": "Person's current whereabouts",
    "reported_symptoms": "Symptoms described on gas call",
}

# These are chronological observation lists. A new detail adds context rather
# than replacing an earlier symptom, landmark, or hazard. Corrections remain
# explicit in the quoted wording and go through operator review.
OBSERVATION_FIELDS = {"symptom_details", "fire_observations", "scene_hazards", "location_context", "people_details"}

# Each question has an English and Spanish version and asks only for an
# observation. No instruction requires the caller to approach a hazard.
QUESTIONS = {
    "symptom_details": ("What other symptoms or changes have you noticed?", "¿Qué otros síntomas o cambios ha notado?"),
    "fire_observations": ("Where have you seen smoke or flames, and has that changed?", "¿Dónde ha visto humo o llamas y ha cambiado eso?"),
    "scene_hazards": ("What hazards have you already noticed nearby?", "¿Qué peligros ha notado cerca?"),
    "location_context": ("What street, building, or unit details do you already know?", "¿Qué detalles de la calle, edificio o apartamento conoce?"),
    "people_details": ("Who is there, and where are they now?", "¿Quién está allí y dónde están ahora?"),
    "location_clarify": ("What street, building number, or nearby landmark can you identify from where you are safely?", "¿Qué calle, número de edificio o punto de referencia puede identificar desde donde está a salvo?"),
    "occupancy": ("What kind of building is it?", "¿Qué tipo de edificio es?"),
    "fire_extent": ("What smoke or flames have you already seen, and where?", "¿Qué humo o llamas ha visto y dónde?"),
    "chief_complaint": ("What symptoms or medical problem are you calling about?", "¿Por qué síntomas o problema médico está llamando?"),
    "awake": ("Is the person awake?", "¿La persona está despierta?"),
    "breathing": ("What can you tell me about the person's breathing?", "¿Qué puede decirme sobre la respiración de la persona?"),
    "persons_involved": ("How many people are involved, as far as you know?", "¿Cuántas personas están involucradas, hasta donde sabe?"),
    "weapons_present": ("Has a weapon been seen or mentioned?", "¿Se ha visto o mencionado un arma?"),
    "incident_timing": ("When did this start, and what has changed since then?", "¿Cuándo empezó esto y qué ha cambiado desde entonces?"),
    "access_notes": ("What entrance, floor, unit, or landmark identifies the location?", "¿Qué entrada, piso, apartamento o punto de referencia identifica el lugar?"),
    "patient_age": ("How old is the person, approximately?", "¿Qué edad tiene la persona, aproximadamente?"),
    "medical_history": ("What relevant medical conditions or medicines do you know about?", "¿Qué enfermedades o medicamentos relevantes conoce?"),
    "vehicle_details": ("How many vehicles are involved, and what do they look like?", "¿Cuántos vehículos están involucrados y cómo son?"),
    "road_conditions": ("Which direction or lanes are affected, and what road hazards have you seen?", "¿Qué sentido o carriles están afectados y qué peligros ha visto en la vía?"),
    "people_accounted_for": ("Where are the people involved, and is anyone's location unknown?", "¿Dónde están las personas involucradas y se desconoce la ubicación de alguien?"),
    "suspect_description": ("What description of the person do you already have?", "¿Qué descripción de la persona tiene?"),
    "suspect_location": ("Where was the person last seen, and are they still there?", "¿Dónde se vio a la persona por última vez y sigue allí?"),
    "gas_source": ("Where did you notice the gas smell or sound?", "¿Dónde notó el olor o sonido de gas?"),
    "water_conditions": ("What kind of water is involved, and what conditions have you seen?", "¿De qué tipo de agua se trata y qué condiciones ha visto?"),
    "last_seen": ("Where was the person last seen in the water?", "¿Dónde se vio a la persona por última vez en el agua?"),
}
QUESTIONS.update({
    "fire_occupants": ("Is everyone out of the building, or is anyone still inside or unaccounted for?", "¿Todos han salido del edificio o queda alguien dentro o sin localizar?"),
    "medical_course": ("Have the symptoms changed since they started?", "¿Han cambiado los síntomas desde que empezaron?"),
    "collision_entrapment": ("Is anyone unable to get out of a vehicle?", "¿Hay alguien que no pueda salir de un vehículo?"),
    "collision_road_blockage": ("Is any lane or part of the road blocked?", "¿Está bloqueado algún carril o parte de la vía?"),
    "crime_presence": ("Is the person still there, or have they left?", "¿La persona sigue allí o se ha ido?"),
    "crime_activity": ("Is the activity you described still happening, or has it stopped?", "¿Sigue ocurriendo lo que describió o ya terminó?"),
    "gas_location": ("Did you notice the gas indoors, outdoors, or both?", "¿Notó el gas dentro, fuera o en ambos lugares?"),
    "gas_exposure": ("Has anyone reported feeling unwell or having symptoms?", "¿Alguien ha dicho que se siente mal o tiene síntomas?"),
    "water_visibility": ("Can you still see the person, or are they out of the water now?", "¿Todavía puede ver a la persona o ya está fuera del agua?"),
    "water_motion": ("Is the water moving or still, from what you have already seen?", "Según lo que ya ha visto, ¿el agua se mueve o está quieta?"),
    "fire_whereabouts": ("What do you already know about where the people inside were last seen?", "¿Qué sabe sobre dónde se vio por última vez a las personas dentro?"),
    "medical_changes": ("What changed in the symptoms, and when did that change happen?", "¿Qué cambió en los síntomas y cuándo ocurrió ese cambio?"),
    "trapped_vehicle": ("Which vehicle is the person unable to leave, and where are they in it?", "¿De qué vehículo no puede salir la persona y en qué parte está?"),
    "blocked_lanes": ("Which lanes or direction are blocked, from what you already know?", "Según lo que sabe, ¿qué carriles o sentido están bloqueados?"),
    "person_left": ("Where was the person last seen, and which direction did they go?", "¿Dónde se vio a la persona por última vez y en qué dirección se fue?"),
    "person_present": ("Where is the person now, from what you already know?", "Según lo que ya sabe, ¿dónde está la persona ahora?"),
    "gas_symptoms": ("What symptoms have been reported and by how many people?", "¿Qué síntomas se han comunicado y cuántas personas los tienen?"),
    "water_last_seen": ("Where and when did you last see the person before losing sight of them?", "¿Dónde y cuándo vio a la persona por última vez antes de perderla de vista?"),
    "water_visible": ("Where exactly can you see the person in relation to a landmark?", "¿Dónde ve exactamente a la persona en relación con un punto de referencia?"),
})

# Model answer -> a more specific wording for a still-missing detail. The
# target is unchanged, so an answered field never turns into a second task.
DECISION_BRANCHES = (
    ("fire_occupants", ("people_inside", "unaccounted"), "occupant_last_seen", "ask_fire_whereabouts"),
    ("medical_course", ("worsening", "improving"), "symptom_changes", "ask_medical_changes"),
    ("collision_entrapment", ("yes",), "trapped_vehicle_location", "ask_trapped_vehicle"),
    ("collision_road_blockage", ("blocked",), "affected_lanes", "ask_blocked_lanes"),
    ("crime_presence", ("left",), "departure_direction", "ask_person_left"),
    ("crime_presence", ("present",), "person_whereabouts", "ask_person_present"),
    ("gas_exposure", ("symptoms_reported",), "reported_symptoms", "ask_gas_symptoms"),
    ("water_visibility", ("not_visible",), "last_seen", "ask_water_last_seen"),
    ("water_visibility", ("visible",), "last_seen", "ask_water_visible"),
)


def question_plan(incident: object, values: dict | None = None) -> tuple[tuple[str, str], ...]:
    specific = SCENARIO_FIELDS.get(incident if isinstance(incident, str) else "", ())
    if not specific:
        return BASE_QUESTIONS
    plan = BASE_QUESTIONS + tuple(
        (name, f"ask_{name}") for name in (*specific, "incident_timing", "access_notes", "location_context", "people_details", "scene_hazards")
    )
    branches = {target: phrase for decision, options, target, phrase in DECISION_BRANCHES
                if DECISIONS[decision][0] == incident and (values or {}).get(decision) in options}
    if known((values or {}).get("location_context")) and not known((values or {}).get("location_text")):
        branches["location_text"] = "ask_location_clarify"
    # A broad note such as "a lane is blocked" does not answer which lane.
    # Decision-triggered detail slots remain separate from that broad note.
    names = {name for name, _ in BASE_QUESTIONS}
    focused = tuple((name, phrase) for name, phrase in branches.items() if name not in names)
    return (tuple((name, branches.get(name, phrase)) for name, phrase in BASE_QUESTIONS)
            + focused + tuple((name, phrase) for name, phrase in plan[len(BASE_QUESTIONS):] if name not in branches))


def known(value: object) -> bool:
    if value is None or value == "unknown" or value == "":
        return False
    if isinstance(value, (list, tuple)):
        return bool(value)
    return True  # False and zero are explicit answers.


# Keep the complete sentence, including negation and uncertainty. These are
# candidate notes for human review, never normalized facts or medical advice.
_DETAIL_CUES = {
    "symptom_details": r"\b(?:chest|breath\w*|droop\w*|slurr\w*|talking|speech|blood|eyebrow|fell|fall|face|arm|eyes are open|cough\w*|burn\w*|pecho|respir\w*|sangre|cara|brazo)\b",
    "fire_observations": r"\b(?:smoke|flames?|fire|hot door|door was hot|humo|llamas?|incendio)\b",
    "scene_hazards": r"\b(?:oxygen|propane|grill|tank|landing|wire|sparks|ox[ií]geno|propano|cable)\b",
    "location_context": r"\b(?:address|street|building|apartment\w*|unit|floor|door|ramp|house|sign|corner|garage|court|lane|avenue|direcci[oó]n|calle|edificio|puerta)\b|\b\d+[A-Z]\b",
    "people_details": r"\b(?:roommate|neighbor|mother|mom|father|son|daughter|kid|boy|girl|people|woman|man|brother|sister|came out|come out|us are out|two of us|didn't answer|madre|hijo|vecin\w*)\b",
    "incident_timing": r"\b(?:started|began|ago|since|at \d{1,2}:\d{2}|empez[oó]|hace \d|desde)\b",
    "access_notes": r"\b(?:entrance|gate|gate code|unit|apartment \w+|floor|alley|behind the building|entrada|port[oó]n|piso|callej[oó]n|apartamento \w+)\b",
    "patient_age": r"\b(?:\d{1,3}[ -](?:years?[ -]old|year-old|months?[ -]old)|aged \d{1,3}|\d{1,3} a[nñ]os|\d{1,2} meses|(?:she|he)(?:'s| is) (?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[ -]\w+)?)\b",
    "medical_history": r"\b(?:history of|takes? (?:medicine|medication|insulin)|diabet\w*|asthma|allerg\w*|antecedentes|medicamento\w*|insulina|asma)\b",
    "vehicle_details": r"\b(?:vehicles?|cars?|trucks?|motorcycles?|sedan|van|bus|veh[ií]culos?|coches?|carros?|cami[oó]n|moto)\b",
    "road_conditions": r"\b(?:lanes?|northbound|southbound|eastbound|westbound|road is|road blocked|traffic blocked|carril\w*|tr[aá]fico|carretera)\b",
    "people_accounted_for": r"\b(?:everyone is|everybody is|all (?:\w+ )?outside|still inside|unaccounted|missing person|trapped|got out|todos (?:est[aá]n|afuera)|dentro|atrapad\w*|no sabemos d[oó]nde)\b",
    "suspect_description": r"\b(?:wearing|dressed in|red jacket|blue jacket|lleva (?:una?|ropa)|vestid\w*|chaqueta|camisa)\b",
    "suspect_location": r"\b(?:suspect|intruder|person) (?:is|was|ran|left|went)|\b(?:ran toward|ran towards|last seen heading|intruso|sospechoso)\b",
    "gas_source": r"\b(?:gas smell|smell (?:of )?gas|smells like gas|hissing|propane|olor a gas|huele a gas|silbido|propano)\b",
    "water_conditions": r"\b(?:current|river|lake|pond|pool|floodwater|ice|corriente|r[ií]o|lago|piscina|inundaci[oó]n)\b",
    "last_seen": r"\b(?:last saw|last seen|went under|[uú]ltima vez|se hundi[oó])\b",
}
_DETAIL_CUES.update({
    "occupant_last_seen": r"\b(?:last (?:saw|seen).*(?:room|floor|inside|bedroom)|(?:person|people|brother|sister).*(?:in the bedroom|on the .*floor)|[uú]ltima vez.*(?:piso|habitaci[oó]n))\b",
    "symptom_changes": r"\b(?:pain|breathing|symptoms?|dolor|respiraci[oó]n).*(?:worse|better|changed|stronger|weaker|empeor|mejor)",
    "trapped_vehicle_location": r"\b(?:driver|passenger|conductor|pasajero).*(?:trapped|pinned|unable|atrapad)|\b(?:trapped|pinned|atrapad\w*).*(?:seat|sedan|van|truck|asiento|coche)",
    "affected_lanes": r"\b(?:(?:north|south|east|west)bound|(?:left|right|middle|both|all|two|three) lanes?|carril (?:izquierdo|derecho)|sentido (?:norte|sur|este|oeste))\b",
    "departure_direction": r"\b(?:ran|went|fled|left|huy[oó]|se fue).*(?:north|south|east|west|toward|hacia|norte|sur)",
    "person_whereabouts": r"\b(?:person|suspect|intruder|sospechoso|intruso).*(?:in the|behind|beside|near the|at the|detr[aá]s|junto a|en la|en el)",
    "reported_symptoms": r"\b(?:dizzy|headache|nausea|coughing|maread\w*|dolor de cabeza|n[aá]useas|tosiendo)\b",
})


def extract_details(text: str) -> dict[str, str]:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?;])\s+|\n+", text) if part.strip()]
    return {
        name: " ".join(sentence for sentence in sentences if re.search(pattern, sentence, re.I))
        for name, pattern in _DETAIL_CUES.items()
        if any(re.search(pattern, sentence, re.I) for sentence in sentences)
    }
