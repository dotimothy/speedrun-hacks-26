"""The incident narrative, assembled rather than generated.

Requirement 5 of issue #1 says any generated narrative "remains a draft until
reviewed and must not invent facts". A template satisfies the second half
structurally: it can only emit words that came from an accepted value or from
its own fixed scaffolding, so there is no mechanism by which a fact could
appear that nobody accepted.

A language model would read better and would need a guard nobody has time to
validate. The limitation is the feature.

Unknown fields are written as "not stated" rather than omitted. An omission
reads as an absence of a problem; "not stated" reads as a gap, which is what
it is.
"""

from __future__ import annotations

from .models import FieldValue, Presence
from ..scenarios import DETAIL_LABELS, question_plan
from ..decision.scenario_tasks import DECISION_LABELS

_LABELS = {
    "incident_type": "Reported incident",
    "caller_name": "Caller",
    "chief_complaint": "Chief complaint",
    "awake": "Awake",
    "breathing": "Breathing",
    "occupancy": "Occupancy",
    "fire_extent": "Fire extent",
    "location_text": "Location",
    "access_notes": "Access",
    "injuries": "Injuries",
    "persons_involved": "People involved",
    "hazards": "Hazards mentioned",
    "weapons_present": "Weapon mentioned",
    "caller_safe": "Caller away from the danger",
    "callback_number": "Callback number",
}
_LABELS.update(DETAIL_LABELS)
_LABELS.update(DECISION_LABELS)

_SECTIONS = (
    ("Incident and contact", ("incident_type", "location_text", "caller_name", "callback_number", "incident_timing", "access_notes")),
    ("People and reported conditions", ("persons_involved", "injuries", "caller_safe", "people_accounted_for")),
    ("Caller observations", ("symptom_details", "fire_observations", "scene_hazards", "location_context", "people_details")),
    ("Scenario details", ("chief_complaint", "awake", "breathing", "patient_age", "medical_history", "occupancy", "fire_extent", "vehicle_details", "road_conditions", "suspect_description", "suspect_location", "gas_source", "water_conditions", "last_seen")),
    ("Hazards reported", ("hazards", "weapons_present")),
    ("Reviewed scenario decisions", tuple(DECISION_LABELS)),
    ("Decision follow-up details", ("occupant_last_seen", "symptom_changes", "trapped_vehicle_location", "affected_lanes", "departure_direction", "person_whereabouts", "reported_symptoms")),
)


def _render(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "none mentioned"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("_", " ")


def render(
    accepted: dict[str, FieldValue],
    *,
    unresolved: list[str] | None = None,
    open_flags: list[str] | None = None,
) -> str:
    """Build the draft from accepted values only."""
    lines: list[str] = []

    incident = accepted.get("incident_type")
    relevant = {name for name, _ in question_plan(incident.value if incident else "",
                {name: item.value for name, item in accepted.items() if item.presence == Presence.STATED})}
    for title, names in _SECTIONS:
        section = []
        for name in names:
            value = accepted.get(name)
            if title in {"Caller observations", "Scenario details", "Reviewed scenario decisions", "Decision follow-up details"} and name not in relevant and value is None:
                continue
            if value is None or value.presence is Presence.UNKNOWN:
                section.append(f"{_LABELS[name]}: not stated.")
            elif value.presence is Presence.STATED_ABSENT:
                reason = value.note or "caller stated there is none"
                section.append(f"{_LABELS[name]}: none — {reason}.")
            else:
                section.append(f"{_LABELS[name]}: {_render(value.value).rstrip('.')}.")
        if section:
            lines.append(title + "\n" + "\n".join(section))
    body = "\n\n".join(lines)

    corrections = [
        f"{_LABELS.get(name, name.replace('_', ' '))}: "
        + " → ".join(_render(entry.value) for entry in item.history)
        for name, item in accepted.items() if len(item.history) > 1
    ]
    if corrections:
        body += "\n\nRecord revisions\n" + "\n".join(corrections)

    if unresolved:
        body += "\n\nStill unresolved: " + "; ".join(unresolved) + "."
    if open_flags:
        body += "\n\nFor review: " + " ".join(open_flags)

    body += (
        "\n\nDraft assembled from accepted fields only. Not reviewed, not "
        "submitted, and not sent to any dispatch system."
    )
    return body
