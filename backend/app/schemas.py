"""The DispatchIQ API contract. This module is the single source of truth.

Three invariants hold everywhere in this package and are enforced by tests:

1. ``unknown`` is a value distinct from ``false``/``none``. It is never coerced.
2. Fields a human has confirmed are never silently overwritten by a model pass.
3. Every prediction carries a score and either a verified evidence span or an
   explicit ``null``.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------
# Controlled vocabularies
# --------------------------------------------------------------------------
# Every categorical field includes UNKNOWN as a first-class member so the model
# has somewhere to abstain to. Abstention is a real answer, not a failure.


class Occupancy(str, Enum):
    DWELLING = "dwelling"
    MULTI_UNIT = "multi_unit"
    COMMERCIAL = "commercial"
    UNKNOWN = "unknown"


class FireExtent(str, Enum):
    """How much of the building is involved. Only meaningful on a fire or gas call."""

    SMOKE_ONLY = "smoke_only"
    ONE_ROOM = "one_room"
    ONE_FLOOR = "one_floor"
    WHOLE_BUILDING = "whole_building"
    SPREADING = "spreading"
    UNKNOWN = "unknown"


class ChiefComplaint(str, Enum):
    """What the medical caller says is wrong. Only meaningful on a medical call."""

    CHEST_PAIN = "chest_pain"
    TROUBLE_BREATHING = "trouble_breathing"
    UNCONSCIOUS = "unconscious"
    BLEEDING = "bleeding"
    SEIZURE = "seizure"
    UNKNOWN = "unknown"


class IncidentType(str, Enum):
    STRUCTURE_FIRE = "structure_fire"
    MEDICAL = "medical"
    TRAFFIC_COLLISION = "traffic_collision"
    CRIME_IN_PROGRESS = "crime_in_progress"
    GAS_LEAK = "gas_leak"
    WATER_RESCUE = "water_rescue"
    OTHER = "other"
    UNKNOWN = "unknown"


class Injuries(str, Enum):
    NONE = "none"          # the caller said there are no injuries
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"    # nobody said anything about injuries -- NOT "none"


class Hazard(str, Enum):
    SMOKE = "smoke"
    FIRE = "fire"
    GAS = "gas"
    WEAPON = "weapon"
    WATER = "water"
    ELECTRICAL = "electrical"
    VEHICLE = "vehicle"
    NONE = "none"


class Tristate(str, Enum):
    """For questions where "we don't know" must not read as "no"."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class VerificationState(str, Enum):
    UNVERIFIED = "unverified"  # model output, nobody has looked at it
    CONFIRMED = "confirmed"    # a human approved this value as-is
    CORRECTED = "corrected"    # a human replaced the model's value


class FireOccupants(str, Enum):
    ALL_OUT = "all_out"
    PEOPLE_INSIDE = "people_inside"
    UNACCOUNTED = "unaccounted"
    UNKNOWN = "unknown"


class MedicalCourse(str, Enum):
    NEW = "new"
    WORSENING = "worsening"
    UNCHANGED = "unchanged"
    IMPROVING = "improving"
    UNKNOWN = "unknown"


class RoadBlockage(str, Enum):
    BLOCKED = "blocked"
    CLEAR = "clear"
    UNKNOWN = "unknown"


class CrimePresence(str, Enum):
    PRESENT = "present"
    LEFT = "left"
    UNKNOWN = "unknown"


class CrimeActivity(str, Enum):
    ONGOING = "ongoing"
    ENDED = "ended"
    UNKNOWN = "unknown"


class GasLocation(str, Enum):
    INDOORS = "indoors"
    OUTDOORS = "outdoors"
    BOTH = "both"
    UNKNOWN = "unknown"


class GasExposure(str, Enum):
    SYMPTOMS_REPORTED = "symptoms_reported"
    NO_SYMPTOMS = "no_symptoms"
    UNKNOWN = "unknown"


class WaterVisibility(str, Enum):
    VISIBLE = "visible"
    NOT_VISIBLE = "not_visible"
    OUT_OF_WATER = "out_of_water"
    UNKNOWN = "unknown"


class WaterMotion(str, Enum):
    MOVING = "moving"
    STILL = "still"
    UNKNOWN = "unknown"


class SuggestedAction(str, Enum):
    """Note what is absent: there is no action that dispatches anything.

    The enum is the guardrail. A caller of this API cannot be told to send a
    unit, because no such value exists to return.
    """

    AWAIT_MORE_AUDIO = "await_more_audio"
    REQUEST_LANGUAGE_SUPPORT = "request_language_support"
    REQUEST_MISSING_CRITICAL_FIELD = "request_missing_critical_field"
    REQUEST_HUMAN_CONFIRMATION = "request_human_confirmation"
    ESCALATE_TO_HUMAN_NOW = "escalate_to_human_now"
    # Reachable only when a human has confirmed every critical field. No model
    # output can produce it, which is this enum's way of saying that nothing
    # here becomes actionable without a person.
    READY_FOR_HUMAN_SUBMISSION = "ready_for_human_submission"


# Fields that must be present before the form is any use to a dispatcher.
CRITICAL_FIELDS = ("incident_type", "location_text", "callback_number")

# The full typed field set, in display order.
FIELD_ORDER = (
    "incident_type",
    "location_text",
    "callback_number",
    "caller_name",
    "chief_complaint",
    "awake",
    "breathing",
    "occupancy",
    "fire_extent",
    "injuries",
    "persons_involved",
    "hazards",
    "weapons_present",
    "caller_safe",
)


# --------------------------------------------------------------------------
# Response pieces
# --------------------------------------------------------------------------


class EvidenceSpan(BaseModel):
    """A span of transcript that justifies a prediction.

    Offsets are character indices into ``source``'s transcript, half-open
    ``[start, end)``, so the UI can highlight without re-searching. A span only
    exists if it was verified against the transcript -- see ``evidence.py``.
    """

    text: str
    start: int
    end: int
    source: Literal["original", "english"] = "original"
    match: Literal["exact", "normalized", "fuzzy"] = "exact"


class Prediction(BaseModel):
    label: str
    value: object = None
    score: float = Field(ge=0.0, le=1.0)
    evidence: EvidenceSpan | None = None
    verification_state: VerificationState = VerificationState.UNVERIFIED
    # True when the LLM and the deterministic baseline disagreed. The UI shows
    # this; it is the honest signal that the field needs a human.
    conflict: bool = False
    # True when the model quoted evidence that is not in the transcript. The
    # value is forced to unknown in that case.
    evidence_unverified: bool = False
    # Full distribution over the controlled vocabulary, when the engine gives
    # one. The UI renders it so a 0.44 "suspected" cannot masquerade as a fact.
    probabilities: dict[str, float] | None = None
    # Raw probability the engine reported, kept separate from ``score``.
    # ``score`` is ours, derived; this is theirs, untouched.
    raw_probability: float | None = None
    # Which pass produced the value: the caller's own language, or Whisper's
    # English translation.
    decided_on: Literal["original", "english", "both", "rules"] = "original"
    # The value the other language pass chose, when the two disagreed.
    alternative: object = None
    # Why the score is what it is, factor by factor. Shown in the UI: a
    # dispatcher who can see that 0.41 came from a cross-checker disagreement
    # is being told something useful, where a bare 0.41 is just a number.
    breakdown: dict[str, object] | None = None


class LatencyBreakdown(BaseModel):
    asr: float = 0.0
    translate: float = 0.0
    decision: float = 0.0
    tts: float = 0.0
    server_total: float = 0.0
    # Filled in by the client: wall-clock round trip minus server_total.
    network: float | None = None
    end_to_end: float | None = None


class IncidentForm(BaseModel):
    """The suggested form. Every field is nullable; nothing is defaulted.

    ``validate_assignment`` is on because confirmed values arrive from the
    client and are written here by ``workflow.merge_confirmed``. Without it a
    raw string would be stored unvalidated and the response would carry a
    value no enum permits; with it, a bad value raises where the merge already
    catches it and records a note.
    """

    model_config = ConfigDict(validate_assignment=True)

    incident_type: IncidentType = IncidentType.UNKNOWN
    location_text: str | None = None
    callback_number: str | None = None
    caller_name: str | None = None
    incident_timing: str | None = None
    symptom_details: list[str] = Field(default_factory=list)
    fire_observations: list[str] = Field(default_factory=list)
    scene_hazards: list[str] = Field(default_factory=list)
    location_context: list[str] = Field(default_factory=list)
    people_details: list[str] = Field(default_factory=list)
    access_notes: str | None = None
    patient_age: str | None = None
    medical_history: str | None = None
    vehicle_details: str | None = None
    road_conditions: str | None = None
    people_accounted_for: str | None = None
    suspect_description: str | None = None
    suspect_location: str | None = None
    gas_source: str | None = None
    water_conditions: str | None = None
    last_seen: str | None = None
    occupant_last_seen: str | None = None
    symptom_changes: str | None = None
    trapped_vehicle_location: str | None = None
    affected_lanes: str | None = None
    departure_direction: str | None = None
    person_whereabouts: str | None = None
    reported_symptoms: str | None = None
    fire_occupants: FireOccupants = FireOccupants.UNKNOWN
    medical_course: MedicalCourse = MedicalCourse.UNKNOWN
    collision_entrapment: Tristate = Tristate.UNKNOWN
    collision_road_blockage: RoadBlockage = RoadBlockage.UNKNOWN
    crime_presence: CrimePresence = CrimePresence.UNKNOWN
    crime_activity: CrimeActivity = CrimeActivity.UNKNOWN
    gas_location: GasLocation = GasLocation.UNKNOWN
    gas_exposure: GasExposure = GasExposure.UNKNOWN
    water_visibility: WaterVisibility = WaterVisibility.UNKNOWN
    water_motion: WaterMotion = WaterMotion.UNKNOWN
    chief_complaint: ChiefComplaint = ChiefComplaint.UNKNOWN
    awake: Tristate = Tristate.UNKNOWN
    breathing: Tristate = Tristate.UNKNOWN
    occupancy: Occupancy = Occupancy.UNKNOWN
    fire_extent: FireExtent = FireExtent.UNKNOWN
    injuries: Injuries = Injuries.UNKNOWN
    persons_involved: int | None = None
    hazards: list[Hazard] = Field(default_factory=list)
    weapons_present: Tristate = Tristate.UNKNOWN
    caller_safe: Tristate = Tristate.UNKNOWN


# --------------------------------------------------------------------------
# Request / response
# --------------------------------------------------------------------------

WhisperSize = Literal["tiny", "base", "small", "medium", "large-v3"]


class ProcessRequest(BaseModel):
    """Either ``audio`` (multipart upload) or ``transcript`` -- not both.

    ``confirmed`` carries fields the dispatcher has already approved or fixed.
    They are passed straight through and never re-predicted.
    """

    session_id: str
    transcript: str | None = None
    whisper_size: WhisperSize = "small"
    decision_engine: Literal["laya", "rules", "ollama"] = "laya"
    decision_model: str | None = None
    samples: int = Field(default=1, ge=1, le=5)
    confirmed: dict[str, object] = Field(default_factory=dict)


class ProcessResponse(BaseModel):
    session_id: str
    detected_language: str | None = None
    language_probability: float | None = None
    transcript_original: str = ""
    transcript_english: str = ""
    predictions: list[Prediction] = Field(default_factory=list)
    suggested_form: IncidentForm = Field(default_factory=IncidentForm)
    suggested_action: SuggestedAction = SuggestedAction.REQUEST_HUMAN_CONFIRMATION
    missing_fields: list[str] = Field(default_factory=list)
    # Always true. There is no code path that sets this false.
    requires_review: bool = True
    latency_ms: LatencyBreakdown = Field(default_factory=LatencyBreakdown)
    engine: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    # Surfaced in the UI, not buried. Laya's own docs state the shipped
    # checkpoints are over-confident and that its temperatures are not
    # calibrated for this data, so every probability here is raw.
    calibration_warning: str | None = None
    auto_reply: AutoReplySuggestion | None = None


class ApprovedPhrase(BaseModel):
    id: str
    language: str
    text: str


class AutoReplySuggestion(BaseModel):
    """What autonomous mode would say, and why -- or why it is staying quiet.

    Always computed and always returned, even when autonomous mode is off in
    the UI, so the reason is visible rather than implicit. ``phrase_id`` being
    null is itself informative: ``reason`` then says what stopped it.
    """

    phrase_id: str | None = None
    language: str | None = None
    text: str | None = None
    reason: str = ""
    target: str | None = None
    # False when policy forbids speaking at all (an escalation, say), as
    # opposed to simply having nothing to ask.
    allowed: bool = True


class SpeakRequest(BaseModel):
    """Only a phrase id. Free-text synthesis is deliberately not reachable."""

    phrase_id: str
    # Optional: lets the server record what has been said to this caller, so a
    # question is never asked twice.
    session_id: str | None = None
