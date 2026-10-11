"""Turning what was just said into proposed updates.

Pure functions. No model, no network, no clock beyond what is passed in -- so
every rule below is testable in milliseconds, which matters because these
rules are the ones that decide whether a caller's correction is honoured or
quietly lost.

**This module never writes the accepted record.** It returns suggestions. That
is the structural reason a model cannot overwrite an operator: there is no code
path from here to a `FieldValue`.

The decision per field, first match wins:

===========================================  ==========================
nothing accepted yet, a value was inferred   addition
accepted value, inference agrees             confirmation (no suggestion)
accepted value differs, correction cue       correction
accepted value differs, no cue               conflict
two candidates disagree in one pass          conflict
===========================================  ==========================

The cue requirement in row three is the whole defence against silent
overwriting. "Oak and Fourth" arriving later, with no cue, might be a second
location -- a cross street, where the other car ended up, a landmark. Treating
it as a correction would be an overwrite wearing a friendlier label. Without a
cue it is a contradiction, and contradictions go to the operator with both
statements visible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field

from ..schemas import Prediction
from .models import (
    ConflictOption,
    EvidenceMethod,
    EvidenceRef,
    Presence,
    ReviewFlag,
    ReviewFlagKind,
    Segment,
    Suggestion,
    SuggestionStatus,
    UnresolvedItem,
    UpdateKind,
)

# ---------------------------------------------------------------------------
# Offsets: one conversion point
# ---------------------------------------------------------------------------


def utf16_len(text: str) -> int:
    """Length in UTF-16 code units -- what JavaScript's ``.length`` reports."""
    return len(text.encode("utf-16-le")) // 2


def cp_to_utf16(text: str, index: int) -> int:
    """Convert a Python code-point index to a JavaScript string index.

    Everything inside Python -- ``re``, ``evidence.locate``, the normalising
    index map -- works in code points and is correct in those units. Rather
    than rewrite any of it, convert once, here, on the way out.

    Worth knowing what does and does not exercise this: every accented Latin
    character is in the BMP, so "Juárez" is 6 units in both schemes. Only
    astral characters diverge -- emoji, some CJK extensions. The conversion is
    still right, and the fixtures include an emoji so the test proves
    something.
    """
    return utf16_len(text[:index])


# ---------------------------------------------------------------------------
# Cues
# ---------------------------------------------------------------------------

# A caller retracting what they just said. Bilingual because the product claim
# is multilingual and a Spanish correction must not read as a contradiction.
_CORRECTION_CUE = re.compile(
    r"\b(sorry|sorry,|actually|i meant|i mean|no wait|wait,? no|scratch that|"
    r"correction|not\s+\w+|rather|make that|"
    r"perd[oó]n|disculpe|digo|quise decir|no es|mejor dicho|me equivoqu[eé])\b",
    re.IGNORECASE,
)

# A quantity that is real but not a number. "Another person is in the second
# car" is information; turning it into a passenger count would be inventing a
# fact the caller never gave.
_VAGUE_QUANTITY = re.compile(
    r"\b(another (?:person|one)|someone else|somebody else|a couple|a few|several|"
    r"more people|others|some people|"
    r"otra persona|alguien m[aá]s|m[aá]s gente|unos cuantos|varios|algunos)\b",
    re.IGNORECASE,
)

# Fields whose value is a list, where a new value adds rather than replaces.
LIST_FIELDS = {"hazards", "access_notes"}

# Fields a model may never propose without a located span. An invented injury
# is the one false positive with a real-world cost, and it was measured: on a
# near-empty call the model returned injuries=suspected at 0.785.
EVIDENCE_REQUIRED = {"injuries"}


@dataclass
class DiffOutcome:
    suggestions: list[Suggestion] = dataclass_field(default_factory=list)
    superseded: dict[str, str] = dataclass_field(default_factory=dict)
    corroborated: dict[str, list[str]] = dataclass_field(default_factory=dict)
    flags: list[ReviewFlag] = dataclass_field(default_factory=list)
    unresolved: list[UnresolvedItem] = dataclass_field(default_factory=list)
    notes: list[str] = dataclass_field(default_factory=list)


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value == "unknown"
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def _locate_in_segment(text: str, needle: str, segment: Segment) -> EvidenceRef | None:
    """Anchor a value to the segment it was spoken in, in UTF-16 units."""
    for layer, body in (("original", segment.original_text), ("translated", segment.translation)):
        if not body or not needle:
            continue
        index = body.find(needle)
        if index == -1:
            lowered = body.lower().find(needle.lower())
            if lowered == -1:
                continue
            index = lowered
        return EvidenceRef(
            segment_id=segment.id,
            source=layer,  # type: ignore[arg-type]
            text=body[index : index + len(needle)],
            start=cp_to_utf16(body, index),
            end=cp_to_utf16(body, index + len(needle)),
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            method=EvidenceMethod.RULE_VERBATIM,
        )
    return None


def has_correction_cue(text: str) -> bool:
    return bool(_CORRECTION_CUE.search(text or ""))


def apply_ordinal_correction(accepted: str, segment_text: str) -> str | None:
    """Rebuild a corrected intersection from a partial retraction.

    "Sorry, Fourth, not Third" names the new ordinal and the old one but not
    the street, so there is no complete location to extract -- which is why the
    issue's own worked example produces nothing from a plain extractor pass.

    The substitution only runs when all three conditions hold: a correction
    cue, an accepted value to correct, and exactly one new ordinal that differs
    from one already present. Narrow on purpose; anything looser would be
    guessing at what the caller meant.
    """
    from ..decision.rules import _ORDINAL  # local import: keeps the regex in one place

    ordinal = re.compile(rf"\b(?:{_ORDINAL})\b", re.IGNORECASE)
    spoken = ordinal.findall(segment_text)
    in_accepted = ordinal.findall(accepted)
    if not spoken or not in_accepted:
        return None

    stale = {token.lower() for token in in_accepted}
    fresh = [token for token in spoken if token.lower() not in stale]
    if len(set(token.lower() for token in fresh)) != 1:
        return None

    replacement = fresh[0]
    return ordinal.sub(
        lambda m: replacement if m.group(0).lower() in stale else m.group(0),
        accepted,
        count=1,
    )


def build_suggestions(
    *,
    accepted: dict,
    pending: list[Suggestion],
    predictions: list[Prediction],
    segment: Segment,
    base_revision: int,
    next_id,
    rule_candidates: dict[str, list[str]] | None = None,
) -> DiffOutcome:
    """Compare what was just inferred against what has been accepted."""
    outcome = DiffOutcome()
    rule_candidates = rule_candidates or {}
    pending_by_field: dict[str, Suggestion] = {s.field: s for s in pending}
    segment_text = segment.original_text or ""
    cue = has_correction_cue(segment_text)
    if segment.translation:
        cue = cue or has_correction_cue(segment.translation)

    # --- a quantity that is real but not a number --------------------------
    vague = _VAGUE_QUANTITY.search(segment_text)
    if vague:
        outcome.unresolved.append(
            UnresolvedItem(
                id=next_id("unres"),
                field="persons_involved",
                text=(
                    f"Caller referred to additional people without giving a count: "
                    f"“{vague.group(0)}”"
                ),
                evidence=[e for e in [_locate_in_segment(segment_text, vague.group(0), segment)] if e],
            )
        )
        outcome.flags.append(
            ReviewFlag(
                id=next_id("flag"),
                kind=ReviewFlagKind.PERSON_COUNT_UNCERTAIN,
                text="Caller referred to additional people without giving a count — review statement.",
                evidence=[e for e in [_locate_in_segment(segment_text, vague.group(0), segment)] if e],
            )
        )

    for prediction in predictions:
        name = prediction.label
        value = prediction.value

        # Rule 0: an absence of evidence never proposes erasing a value.
        if _is_empty(value):
            continue

        # Rule 1: some fields may not be proposed on a model's say-so alone.
        if name in EVIDENCE_REQUIRED and prediction.evidence is None:
            continue

        current = accepted.get(name)
        evidence = _evidence_for_prediction(prediction, segment)

        # Rule 2: already accepted and unchanged -- strengthen, do not restate.
        if current is not None and current.value == value:
            existing = pending_by_field.get(name)
            if existing is not None:
                outcome.corroborated.setdefault(existing.id, []).append(segment.id)
            continue

        # Rule 3: a list field gains items rather than being replaced.
        if name in LIST_FIELDS and isinstance(value, list):
            known = set(current.value or []) if current and isinstance(current.value, list) else set()
            added = [item for item in value if item not in known]
            if not added:
                continue
            outcome.suggestions.append(
                _make(next_id, name, sorted(known | set(added)), UpdateKind.ADDITION,
                      base_revision, prediction, evidence,
                      previous=current.value if current else None)
            )
            continue

        # Rule 4: nothing accepted -- a plain addition.
        if current is None or _is_empty(current.value):
            suggestion = _make(next_id, name, value, UpdateKind.ADDITION,
                               base_revision, prediction, evidence)
            _supersede_or_conflict(outcome, pending_by_field.get(name), suggestion, segment)
            outcome.suggestions.append(suggestion)
            continue

        # Rules 5 and 6: it differs from something a human accepted.
        proposed = value
        if cue and isinstance(current.value, str) and isinstance(value, str):
            rebuilt = apply_ordinal_correction(current.value, segment_text)
            if rebuilt and rebuilt != current.value:
                proposed = rebuilt

        kind = UpdateKind.CORRECTION if cue else UpdateKind.CONFLICT
        suggestion = _make(next_id, name, proposed if cue else None, kind,
                           base_revision, prediction, evidence,
                           previous=current.value)
        suggestion.prior_statement = _statement_for(current, accepted)
        if not cue:
            suggestion.value_kind = "unresolved"
            suggestion.conflict_options = [
                ConflictOption(option_id="accepted", value=current.value,
                               statement=str(current.value),
                               segment_id=(current.source_segment_ids or [segment.id])[0]),
                ConflictOption(option_id="new", value=value, statement=segment_text,
                               segment_id=segment.id, evidence=evidence),
            ]
            outcome.flags.append(
                ReviewFlag(
                    id=next_id("flag"),
                    kind=ReviewFlagKind.CONTRADICTORY_STATEMENTS,
                    text=(
                        f"Statements about {name.replace('_', ' ')} disagree "
                        f"— review both."
                    ),
                    evidence=[e for e in [evidence] if e],
                )
            )
        outcome.suggestions.append(suggestion)

    return outcome


def _statement_for(current, accepted) -> str | None:
    return str(current.value) if current is not None else None


def _evidence_for_prediction(prediction: Prediction, segment: Segment) -> EvidenceRef | None:
    """Re-anchor the v1 evidence span onto this segment, honestly labelled."""
    span = prediction.evidence
    if span is None:
        return None
    body = segment.original_text if span.source == "original" else (segment.translation or "")
    index = body.find(span.text) if body else -1
    if index == -1:
        # The span came from an earlier segment in the cumulative text. Keep
        # the quote, drop the offsets rather than point at the wrong words.
        return EvidenceRef(
            segment_id=segment.id,
            source="original" if span.source == "original" else "translated",
            text=span.text,
            start=None,
            end=None,
            method=EvidenceMethod.KEYWORD_CONTEXT,
        )
    return EvidenceRef(
        segment_id=segment.id,
        source="original" if span.source == "original" else "translated",
        text=span.text,
        start=cp_to_utf16(body, index),
        end=cp_to_utf16(body, index + len(span.text)),
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        # Laya does not say which words produced its answer, so a located
        # domain term is context, not an explanation.
        method=EvidenceMethod.KEYWORD_CONTEXT,
    )


def _make(
    next_id,
    name: str,
    value: object,
    kind: UpdateKind,
    base_revision: int,
    prediction: Prediction,
    evidence: EvidenceRef | None,
    previous: object = None,
) -> Suggestion:
    from .models import RawModelScore, SupportIndicators

    breakdown = prediction.breakdown or {}
    return Suggestion(
        id=next_id("sug"),
        field=name,
        kind=kind,
        proposed_value=value,
        previous_value=previous,
        base_revision=base_revision,
        proposed_presence=Presence.STATED,
        value_kind=_value_kind(value),
        evidence=[e for e in [evidence] if e],
        support=SupportIndicators(
            derived_score=prediction.score,
            cross_check=str(breakdown.get("cross_check", "unavailable")),  # type: ignore[arg-type]
            rule_check=str(breakdown.get("rule_check", "no_opinion")),  # type: ignore[arg-type]
            uncorroborated=bool(breakdown.get("uncorroborated")),
            note=str(breakdown.get("uncorroborated")) if breakdown.get("uncorroborated") else None,
        ),
        raw_model=(
            RawModelScore(
                engine="laya",
                probability=prediction.raw_probability,
                probabilities=prediction.probabilities,
            )
            if prediction.raw_probability is not None
            else None
        ),
    )


def _value_kind(value: object) -> str:
    if isinstance(value, bool):
        return "enum"
    if isinstance(value, int):
        return "int"
    if isinstance(value, list):
        return "list"
    return "text"


def _supersede_or_conflict(
    outcome: DiffOutcome, existing: Suggestion | None, fresh: Suggestion, segment: Segment
) -> None:
    """A newer pending suggestion on the same field retires the older one.

    Except a conflict, which is never superseded: it is a question waiting for
    a human, and replacing it would answer that question by machine.
    """
    if existing is None or existing.status is not SuggestionStatus.PENDING:
        return
    if existing.kind is UpdateKind.CONFLICT:
        existing.conflict_options.append(
            ConflictOption(
                option_id=f"opt-{len(existing.conflict_options) + 1}",
                value=fresh.proposed_value,
                statement=segment.original_text,
                segment_id=segment.id,
            )
        )
        return
    if existing.proposed_value == fresh.proposed_value:
        outcome.corroborated.setdefault(existing.id, []).append(segment.id)
        return
    outcome.superseded[existing.id] = fresh.id
