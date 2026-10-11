"""Deciding, deterministically, whether the system may speak on its own.

Autonomous mode is the one feature in this project that acts on a caller
without a human pressing anything first, so the rules are written out here in
one short, testable place rather than spread through the UI.

What it is allowed to do
------------------------
Ask a question the dispatcher would ask anyway -- the street number, a callback
number, whether anyone is hurt -- drawn from the fixed phrase table in
``tts.py``. That is the whole capability.

What it must never do
---------------------
* **Speak during an escalation.** If a weapon is indicated or the caller says
  they are trapped, the system goes quiet and hands over. A machine talking
  over the worst moment of a call is the exact failure worth designing out.
* **Say anything not in the approved table.** There is no path from here to
  free text; this module returns a phrase *id* and nothing else.
* **Promise anything.** No phrase in the table says help is coming, gives an
  arrival time, or implies that anything has been dispatched.
* **Touch the form.** Autonomous replies gather information. They never
  confirm a field, and ``requires_review`` is unaffected.
* **Talk over itself.** One open question at a time, and never the same
  question twice in a row for a session.

Everything here is a pure function of the workflow result, so the policy can
be read and tested without a model, a microphone or a speaker.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schemas import SuggestedAction
from .workflow import is_unknown
from .scenarios import BASE_QUESTIONS, known, question_plan

# Actions during which the system stays silent, and why.
SILENT_ACTIONS: dict[str, str] = {
    SuggestedAction.ESCALATE_TO_HUMAN_NOW.value: (
        "a weapon or a trapped caller was indicated; handing to a human instead of speaking"
    ),
    SuggestedAction.AWAIT_MORE_AUDIO.value: (
        "no speech has been heard yet; there is nothing to reply to"
    ),
    SuggestedAction.READY_FOR_HUMAN_SUBMISSION.value: (
        "every critical field is confirmed; the dispatcher takes it from here"
    ),
}

# Which question to ask for a missing field, in the order we would ask them.
# Location first: it is the field a call cannot do without.
FIELD_PHRASES = BASE_QUESTIONS

SUPPORTED_LANGUAGES = ("en", "es")


@dataclass
class AutoReply:
    phrase_id: str
    language: str
    reason: str
    # The field this question is trying to fill, for the audit line.
    target: str | None = None


def _phrase_for(base: str, language: str) -> str:
    return f"{base}_{language}"


def decide(
    *,
    suggested_action: str,
    predictions: list,
    detected_language: str | None,
    confirmed: dict,
    already_asked: list[str],
) -> tuple[AutoReply | None, str]:
    """Return the phrase to speak, or None with the reason for staying quiet.

    ``already_asked`` is the session's history of auto-spoken phrase ids. A
    question is not repeated: if the caller did not answer the first time,
    asking again in the same synthetic voice will not help, and a dispatcher
    talking to a frightened person does not need a machine looping at them.
    """
    if suggested_action in SILENT_ACTIONS:
        return None, SILENT_ACTIONS[suggested_action]

    language = (detected_language or "en")[:2]
    if language not in SUPPORTED_LANGUAGES:
        # Better to say nothing than to ask a Portuguese speaker a question in
        # Spanish because it is the closest voice installed.
        return None, f"no approved phrases in the caller's language ({language})"

    if suggested_action == SuggestedAction.REQUEST_LANGUAGE_SUPPORT.value:
        phrase = _phrase_for("interpreter", language)
        if phrase in already_asked:
            return None, "the interpreter line has already been played"
        return AutoReply(phrase, language, "the caller's language is uncertain"), ""

    by_label = {getattr(p, "label", None): p for p in predictions}

    incident = confirmed.get("incident_type", getattr(by_label.get("incident_type"), "value", None))
    values = {name: getattr(prediction, "value", None) for name, prediction in by_label.items()}
    values.update(confirmed)
    for field, base in question_plan(incident, values):
        if field in confirmed and known(confirmed[field]):
            continue
        prediction = by_label.get(field)
        if prediction is None or is_unknown(getattr(prediction, "value", None)):
            phrase = _phrase_for(base, language)
            if phrase in already_asked:
                continue  # asked once already; move to the next gap
            return (
                AutoReply(phrase, language, f"{field} is still unknown", target=field),
                "",
            )

    return None, "nothing is missing that a scripted question would fill"
