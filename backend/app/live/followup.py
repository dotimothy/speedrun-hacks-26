"""One question plan shared by the live operator view and phone transport."""
from __future__ import annotations

from ..scenarios import DECISION_BRANCHES, known, question_plan
from ..decision.scenario_tasks import DECISIONS, SCENARIO_TASKS, DECISION_LABELS
from ..tts import APPROVED
from .models import Presence, UpdateKind


def reviewed_incident(snapshot) -> str | None:
    item = snapshot.accepted_record.get("incident_type")
    if item and item.presence == Presence.STATED and not any(
        s.field == "incident_type" and s.kind == UpdateKind.CONFLICT for s in snapshot.pending
    ):
        return item.value
    return None


def observed(snapshot) -> dict[str, object]:
    values = {
        name: ("stated_absent" if item.presence == Presence.STATED_ABSENT else item.value)
        for name, item in snapshot.accepted_record.items()
        if item.presence != Presence.UNKNOWN
    }
    for suggestion in snapshot.pending:
        if suggestion.kind == UpdateKind.CONFLICT:
            values[suggestion.field] = None
        elif suggestion.field not in values and known(suggestion.proposed_value):
            values[suggestion.field] = suggestion.proposed_value
    return values


def follow_up_questions(snapshot) -> list[dict]:
    values = observed(snapshot)
    language = (snapshot.detected_language or "en")[:2]
    # Operator-readable prompts still appear for an unsupported language;
    # the audio bridge independently refuses to speak in that case.
    language = language if language in ("en", "es") else "en"
    conflicts = {s.field for s in snapshot.pending if s.kind == UpdateKind.CONFLICT}
    reasons = {target: f"{DECISION_LABELS[decision]}: {str(values[decision]).replace('_', ' ')} (review interpretation)"
               for decision, options, target, phrase in DECISION_BRANCHES
               if DECISIONS[decision][0] == values.get("incident_type") and values.get(decision) in options}
    return [
        {
            "field": name,
            "phrase_id": f"{base}_{language}",
            "text": APPROVED[f"{base}_{language}"][1],
            "reason": "Conflicting statements need clarification" if name in conflicts else reasons.get(name, "Not yet established"),
        }
        for name, base in question_plan(values.get("incident_type"), values)
        if not known(values.get(name))
    ]


def decision_focus(snapshot) -> dict | None:
    values = observed(snapshot)
    incident = values.get("incident_type")
    if not isinstance(incident, str) or incident not in SCENARIO_TASKS:
        return None
    task = SCENARIO_TASKS[incident]
    rows = []
    for name in task["fields"]:
        pending = next((s for s in snapshot.pending if s.field == name), None)
        accepted = snapshot.accepted_record.get(name)
        rows.append({
            "field": name,
            "label": DECISION_LABELS.get(name, name.replace("_", " ").capitalize()),
            "value": values.get(name, "unknown"),
            "status": "conflict" if pending and pending.kind == UpdateKind.CONFLICT else (
                "accepted" if accepted and accepted.presence != Presence.UNKNOWN else (
                    "pending_review" if pending else "not_established")),
            "suggestion_id": pending.id if pending else None,
        })
    return {"scenario": incident, "title": task["title"], "purpose": task["purpose"], "decisions": rows}
