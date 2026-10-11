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

# Order is narrative order, not form order: what happened, then where, then
# who, then how to get back in touch.
_SENTENCES: tuple[tuple[str, str], ...] = (
    ("incident_type", "Reported incident: {value}."),
    ("caller_name", "Caller: {value}."),
    ("chief_complaint", "Chief complaint: {value}."),
    ("awake", "Awake: {value}."),
    ("breathing", "Breathing: {value}."),
    ("occupancy", "Occupancy: {value}."),
    ("fire_extent", "Fire extent: {value}."),
    ("location_text", "Location: {value}."),
    ("access_notes", "Access: {value}."),
    ("injuries", "Injuries: {value}."),
    ("persons_involved", "People involved: {value}."),
    ("hazards", "Hazards mentioned: {value}."),
    ("weapons_present", "Weapon mentioned: {value}."),
    ("caller_safe", "Caller away from the danger: {value}."),
    ("callback_number", "Callback number: {value}."),
)

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

    for name, template in _SENTENCES:
        value = accepted.get(name)
        if value is None or value.presence is Presence.UNKNOWN:
            # Named and marked, not dropped.
            lines.append(f"{_LABELS[name]}: not stated.")
            continue
        if value.presence is Presence.STATED_ABSENT:
            reason = value.note or "caller stated there is none"
            lines.append(f"{_LABELS[name]}: none — {reason}.")
            continue
        lines.append(template.format(value=_render(value.value)))

    body = " ".join(lines)

    if unresolved:
        body += "\n\nStill unresolved: " + "; ".join(unresolved) + "."
    if open_flags:
        body += "\n\nFor review: " + " ".join(open_flags)

    body += (
        "\n\nDraft assembled from accepted fields only. Not reviewed, not "
        "submitted, and not sent to any dispatch system."
    )
    return body
