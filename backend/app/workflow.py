"""The deterministic layer. No model, no randomness, no I/O.

Everything a dispatcher is told to do next is decided here, by a table that can
be read in one sitting and tested without a GPU. That split is deliberate: the
models produce suggestions, and this module decides what may be said about
them.

Three of the rules are structural rather than conditional:

* ``requires_review`` is a constant ``True``. There is no branch that clears it.
* No member of ``SuggestedAction`` dispatches anything, so no amount of model
  confidence can produce a dispatch.
* ``ready_for_human_submission`` is reachable only once a human has confirmed
  every critical field, so model output cannot reach it at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import (
    CRITICAL_FIELDS,
    IncidentForm,
    Prediction,
    SuggestedAction,
    VerificationState,
)

# What to ask the caller when a critical field is missing. A static lookup, so
# the words a dispatcher reads out are never generated text.
FIELD_PROMPTS: dict[str, str] = {
    "incident_type": "Ask the caller to describe what is happening right now.",
    "location_text": "Ask for the street number and street name, and repeat it back.",
    "callback_number": "Ask for a number you can call back on.",
    "injuries": "Ask whether anyone is hurt.",
    "weapons_present": "Ask whether there is a weapon at the scene.",
    "caller_safe": "Ask whether the caller is somewhere safe.",
}

# Values that mean "we do not know", as distinct from a real negative answer.
UNKNOWN_VALUES = {None, "unknown", ""}


def is_unknown(value: object) -> bool:
    """``unknown`` is not ``false``.

    ``injuries="none"`` is a caller saying nobody is hurt. ``injuries="unknown"``
    is nobody having said. Collapsing those two is the specific failure this
    function exists to prevent, so ``"none"``, ``"no"`` and ``0`` are
    deliberately NOT unknown -- they are answers.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return value in UNKNOWN_VALUES


@dataclass
class WorkflowResult:
    predictions: list[Prediction]
    form: IncidentForm
    suggested_action: SuggestedAction
    missing_fields: list[str]
    prompts: list[str] = field(default_factory=list)
    requires_review: bool = True
    notes: list[str] = field(default_factory=list)


def merge_confirmed(
    predictions: list[Prediction],
    form: IncidentForm,
    confirmed: dict[str, object],
) -> tuple[list[Prediction], IncidentForm, list[str]]:
    """Carry human-confirmed values through untouched.

    A confirmed field keeps the human's value, the model's fresh suggestion is
    preserved beside it in ``alternative``, and ``conflict`` is set when the two
    differ so the UI can show that the model now disagrees with the dispatcher.
    What never happens is the model's value replacing theirs.
    """
    notes: list[str] = []
    merged: list[Prediction] = []

    for pred in predictions:
        human_value = confirmed.get(pred.label, ...)
        if human_value is ...:
            merged.append(pred)
            continue

        model_value = pred.value
        differs = model_value != human_value
        merged.append(
            pred.model_copy(
                update={
                    "value": human_value,
                    # A human has looked at it, so the number beside it is no
                    # longer a model's claim.
                    "score": 1.0,
                    "verification_state": (
                        VerificationState.CORRECTED if differs else VerificationState.CONFIRMED
                    ),
                    "alternative": model_value if differs else None,
                    "conflict": differs,
                    "breakdown": {
                        "source": "human",
                        "model_suggested": model_value,
                        "overridden": differs,
                    },
                }
            )
        )
        if differs:
            notes.append(
                f"{pred.label}: keeping the confirmed value; the model now "
                f"suggests {model_value!r}."
            )

    updated = form.model_copy()
    for label, value in confirmed.items():
        if hasattr(updated, label):
            try:
                setattr(updated, label, value)
            except Exception:
                # A confirmed value that will not validate is a client bug, not
                # grounds for silently dropping the dispatcher's input.
                notes.append(f"{label}: confirmed value {value!r} failed validation.")
    return merged, updated, notes


def decide_action(
    predictions: list[Prediction],
    *,
    has_speech: bool,
    language_probability: float | None,
    confirmed: dict[str, object],
) -> tuple[SuggestedAction, list[str], list[str]]:
    """First match wins. The order of these checks *is* the policy."""
    by_label = {p.label: p for p in predictions}

    missing: list[str] = []
    for label in CRITICAL_FIELDS:
        if label in confirmed:
            continue
        pred = by_label.get(label)
        if pred is None or is_unknown(pred.value):
            missing.append(label)
    prompts = [FIELD_PROMPTS[label] for label in missing if label in FIELD_PROMPTS]

    # 1. Nothing was heard. VAD returning no segments is a real answer, and
    #    filling in a form from silence would be inventing an incident.
    if not has_speech:
        return SuggestedAction.AWAIT_MORE_AUDIO, missing, [
            "No speech detected. Keep listening rather than filling the form."
        ]

    # 2. If the language itself is in doubt, so is every field drawn from it.
    if language_probability is not None and language_probability < 0.5:
        return SuggestedAction.REQUEST_LANGUAGE_SUPPORT, missing, [
            "Language could not be identified confidently. Consider an interpreter."
        ]

    # 3. Safety-relevant answers outrank form completeness.
    weapons = by_label.get("weapons_present")
    safe = by_label.get("caller_safe")
    if (weapons is not None and weapons.value == "yes") or (
        safe is not None and safe.value == "no"
    ):
        return SuggestedAction.ESCALATE_TO_HUMAN_NOW, missing, [
            "A weapon or an unsafe caller was indicated. Escalate to a human now."
        ]

    # 4. The form is not usable without these.
    if missing:
        return SuggestedAction.REQUEST_MISSING_CRITICAL_FIELD, missing, prompts

    # 5. Only a human can get us here.
    if all(label in confirmed for label in CRITICAL_FIELDS):
        return SuggestedAction.READY_FOR_HUMAN_SUBMISSION, [], [
            "Every critical field has been confirmed by a human."
        ]

    return SuggestedAction.REQUEST_HUMAN_CONFIRMATION, [], prompts


def apply(
    predictions: list[Prediction],
    form: IncidentForm,
    *,
    confirmed: dict[str, object] | None = None,
    has_speech: bool = True,
    language_probability: float | None = None,
) -> WorkflowResult:
    confirmed = confirmed or {}
    predictions, form, notes = merge_confirmed(predictions, form, confirmed)
    action, missing, prompts = decide_action(
        predictions,
        has_speech=has_speech,
        language_probability=language_probability,
        confirmed=confirmed,
    )
    return WorkflowResult(
        predictions=predictions,
        form=form,
        suggested_action=action,
        missing_fields=missing,
        prompts=prompts,
        requires_review=True,  # constant, by design
        notes=notes,
    )
