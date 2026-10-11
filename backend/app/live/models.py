"""The live-session wire contract (API v2).

This is the shared schema issue #1 asks the backend to commit before frontend
integration. The shape it describes is a call-taking assistant rather than a
form-filler: as a conversation unfolds, the system proposes *updates* to an
incident record that the operator owns.

The central idea is two layers that never merge:

* **Suggestions** -- what the models inferred. Each has a lifecycle, a reason,
  and the words it came from. Nothing here is fact.
* **The accepted record** -- what an operator approved, with the revision it
  was approved at and the segments it came from. Model output can never write
  to this layer; only a review action can.

Three distinctions the rest of the file exists to preserve:

``unknown`` is not a negative
    A field absent from ``accepted_record`` was never established. A field
    holding ``unknown`` was asked about and not answered. A field holding
    ``none``/``no`` is a caller saying so. The enums come from ``schemas.py``
    unchanged, because they already draw this line.

Support is not probability
    ``SupportIndicators`` is ours -- cross-checks and heuristics.
    ``RawModelScore`` is the model's, untransformed, and carries
    ``calibrated=False`` because it is not a probability of correctness.
    They are separate objects so they cannot be read as one number.

Context is not an explanation
    ``EvidenceMethod`` says how a span was found. A keyword we went looking
    for is context; it is not the model explaining itself, and the API should
    not let a client mistake one for the other.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


class Speaker(str, Enum):
    CALLER = "caller"
    OPERATOR = "operator"
    INTERPRETER = "interpreter"
    # Explicit rather than defaulted: a segment whose attribution we do not
    # know must not be silently treated as the caller, because everything the
    # caller is taken to have said becomes evidence.
    UNKNOWN = "unknown"


class Segment(BaseModel):
    """One finalized utterance.

    ``start_ms``/``end_ms`` are integer milliseconds **from session creation**,
    not from the start of the clip this utterance arrived in. Null when the
    input was typed, because there is no audio to point at and a zero would
    read as "the very beginning of the call".
    """

    id: str
    index: int
    speaker: Speaker = Speaker.UNKNOWN
    original_text: str
    language: str | None = None
    # English. Null when the original already is English -- null means "no
    # translation exists", never "translation failed".
    translation: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    final: bool = True
    received_at: str = Field(default_factory=utcnow)
    # Echoed back so a client can match its retry to the stored segment.
    client_segment_id: str | None = None


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class Presence(str, Enum):
    """Whether a field has been spoken to at all.

    The enums in ``schemas.py`` already separate ``unknown`` from ``none`` for
    the categorical fields. The free-text ones have no such vocabulary:
    ``location_text = None`` means "nobody has said" and ``callback_number =
    None`` could mean "the caller has no phone", and nothing in the value
    distinguishes them. This does.

    It matters beyond tidiness: ``missing_fields`` is computed from ``unknown``
    only, so a caller who says they have no phone stops being prompted for
    one, while a caller nobody has asked keeps being prompted.
    """

    UNKNOWN = "unknown"              # nobody has said anything
    STATED = "stated"                # a value was given (including "none"/"no"/0)
    STATED_ABSENT = "stated_absent"  # the caller said there is none, or refused


class EvidenceMethod(str, Enum):
    """How the cited span was found. Not interchangeable.

    ``rule_verbatim`` is the strongest: a deterministic pattern matched exactly
    this text, so the span *is* the value. ``model_quoted`` is a generative
    model's claimed quote, validated against the transcript before being
    stored. ``keyword_context`` is the weakest and the easiest to overread --
    we searched for a domain term and widened to its sentence. It shows the
    operator where to look; it is not the model explaining its answer and not
    proof the value is supported.
    """

    RULE_VERBATIM = "rule_verbatim"
    MODEL_QUOTED = "model_quoted"
    KEYWORD_CONTEXT = "keyword_context"
    NONE = "none"


class EvidenceRef(BaseModel):
    """A span of one segment, anchored so Python and JavaScript agree.

    Offsets are **UTF-16 code units into that segment's own text**. JavaScript
    string indices are UTF-16 natively; Python's are code points, and the
    Spanish scenarios contain accented characters where the two disagree. The
    unit is stated in the payload rather than assumed.

    Offsets index a single segment, never a concatenated transcript, so a span
    stored early in a call still points at the same words after fifty more
    utterances arrive.

    ``text`` is always included, so a client that distrusts the offsets can
    locate the span itself.
    """

    segment_id: str
    source: Literal["original", "translated"] = "original"
    text: str
    start: int | None = None
    end: int | None = None
    offset_unit: Literal["utf16"] = "utf16"
    start_ms: int | None = None
    end_ms: int | None = None
    method: EvidenceMethod = EvidenceMethod.NONE


# ---------------------------------------------------------------------------
# Confidence, kept in two separate boxes
# ---------------------------------------------------------------------------


class SupportIndicators(BaseModel):
    """Our heuristics. Derived by ``confidence.py``, not read from a model."""

    derived_score: float = Field(ge=0.0, le=1.0, default=0.0)
    cross_check: Literal["agree", "disagree", "unavailable", "no_opinion"] = "unavailable"
    rule_check: Literal["agree", "disagree", "no_opinion"] = "no_opinion"
    asr_confidence: float | None = None
    uncorroborated: bool = False
    note: str | None = None


class RawModelScore(BaseModel):
    """The model's own number, untransformed.

    ``calibrated`` is a constant ``False`` and not a configuration option.
    Laya's shipped checkpoints are over-confident by its own documentation, and
    this field exists so no client can present the number as a probability of
    correctness.
    """

    engine: str
    checkpoint: str | None = None
    probability: float | None = None
    probabilities: dict[str, float] | None = None
    calibrated: Literal[False] = False


# ---------------------------------------------------------------------------
# Suggestions and the accepted record
# ---------------------------------------------------------------------------


class UpdateKind(str, Enum):
    ADDITION = "addition"        # nothing accepted for this field yet
    CORRECTION = "correction"    # the caller corrected an accepted value
    CONFLICT = "conflict"        # statements disagree and nobody has said which wins
    CONFIRMATION = "confirmation"  # restates what is already accepted


class SuggestionStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EDITED = "edited"        # accepted with an operator's own value
    REJECTED = "rejected"
    SUPERSEDED = "superseded"  # a later suggestion replaced it before review


class ConflictOption(BaseModel):
    """One side of a contradiction, kept in the caller's own words."""

    option_id: str
    value: object
    statement: str
    segment_id: str
    evidence: EvidenceRef | None = None


class Suggestion(BaseModel):
    id: str
    field: str
    kind: UpdateKind
    # A conflict deliberately has NO proposed value. There is nothing to
    # accept until a human says which statement is right, so offering a value
    # would be the machine quietly resolving a contradiction it detected.
    proposed_value: object = None
    value_kind: Literal["enum", "text", "int", "list", "unresolved"] = "text"
    proposed_presence: Presence = Presence.STATED
    # What the operator would be overwriting. Null for an addition.
    previous_value: object = None
    # The revision this was computed against, so a client can tell whether it
    # is looking at a suggestion formed before its own last action.
    base_revision: int
    status: SuggestionStatus = SuggestionStatus.PENDING
    created_at: str = Field(default_factory=utcnow)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    support: SupportIndicators = Field(default_factory=SupportIndicators)
    raw_model: RawModelScore | None = None
    # Set when a later suggestion took this one's place before review.
    superseded_by: str | None = None
    # Later segments that said the same thing. Repetition strengthens an
    # existing suggestion rather than stacking an identical one beside it --
    # without this, cumulative re-inference re-proposes the same fact on
    # every single append.
    corroborating_segment_ids: list[str] = Field(default_factory=list)
    # Set when an operator accepted with an edited value.
    resolved_value: object = None
    resolved_at: str | None = None
    # For a conflict: the competing statements, and the sibling suggestions.
    conflict_options: list[ConflictOption] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    # The caller's earlier words, when this corrects something already
    # accepted. Both statements must be on screen for the operator to judge.
    prior_statement: str | None = None
    prior_evidence: EvidenceRef | None = None
    note: str | None = None


class FieldRevision(BaseModel):
    """One write to one field. Append-only; nothing is ever edited in place."""

    revision: int
    value: object = None
    presence: Presence = Presence.STATED
    action: Literal["accept", "edit", "reject", "resolve", "direct"] = "accept"
    suggestion_id: str | None = None
    operation_id: str | None = None
    at: str = Field(default_factory=utcnow)
    note: str | None = None


class FieldValue(BaseModel):
    """One accepted fact, with how it got there."""

    field: str
    value: object = None
    presence: Presence = Presence.UNKNOWN
    revision: int
    accepted_at: str = Field(default_factory=utcnow)
    source_segment_ids: list[str] = Field(default_factory=list)
    # The suggestion the operator acted on, or null if they typed it directly.
    suggestion_id: str | None = None
    edited: bool = False
    note: str | None = None
    # Why a value is what it is, and who made it so. Append-only: ``value`` is
    # always ``history[-1].value``, and a correction adds an entry rather than
    # erasing what the caller first said.
    history: list[FieldRevision] = Field(default_factory=list)
    # Set when a statement was heard but could not be pinned to a value --
    # "another person is in the second car" lands here, never in ``value``.
    uncertainty_note: str | None = None


# ---------------------------------------------------------------------------
# Review flags -- what replaced escalation advice
# ---------------------------------------------------------------------------


class ReviewFlagKind(str, Enum):
    WEAPON_MENTIONED = "weapon_mentioned"
    CALLER_MAY_BE_TRAPPED = "caller_may_be_trapped"
    LANGUAGE_UNCERTAIN = "language_uncertain"
    NO_SPEECH_DETECTED = "no_speech_detected"
    TRANSLATION_UNVERIFIED = "translation_unverified"
    CONTRADICTORY_STATEMENTS = "contradictory_statements"
    PERSON_COUNT_UNCERTAIN = "person_count_uncertain"
    MODEL_PASSES_DISAGREED = "model_passes_disagreed"


class ReviewFlag(BaseModel):
    """A fact about the conversation, not an instruction.

    The previous design emitted ``escalate_to_human_now``, which told a
    dispatcher what to do. Urgency and escalation are the operator's call and
    always were; this reports what was said and points at where it was said.
    """

    id: str
    kind: ReviewFlagKind
    text: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    status: Literal["open", "acknowledged"] = "open"
    created_at: str = Field(default_factory=utcnow)
    acknowledged_at: str | None = None


class UnresolvedItem(BaseModel):
    """Something stated but not pinned down.

    This is where "another person is in the second car" goes. It is a real
    statement and it must survive, but turning it into a passenger count would
    be inventing a fact the caller never gave.
    """

    id: str
    field: str | None = None
    text: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Narrative and publication
# ---------------------------------------------------------------------------


class NarrativeDraft(BaseModel):
    """A draft until a human says otherwise.

    ``method`` is ``template``: the text is assembled from accepted values by a
    fixed template. That is a deliberate limitation rather than a shortcut -- a
    template cannot invent a fact, and "must not invent facts" is the
    requirement. An operator's replacement text is kept separately so the
    generated draft can still be diffed against it.
    """

    text: str = ""
    method: Literal["template"] = "template"
    generated_from_revision: int = 0
    edited: bool = False
    editor_text: str | None = None

    @property
    def effective(self) -> str:
        return self.editor_text if self.edited and self.editor_text else self.text


class PublishedSnapshot(BaseModel):
    """What a receiving dispatcher would see. Only ever created by a human."""

    id: str
    revision: int
    published_at: str = Field(default_factory=utcnow)
    record: dict[str, object] = Field(default_factory=dict)
    narrative: str = ""
    unresolved: list[str] = Field(default_factory=list)
    open_flags: list[str] = Field(default_factory=list)
    delta_from: int | None = None
    changed_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------


class Diagnostics(BaseModel):
    """Kept away from the operator surface, per the issue.

    Useful for us and for judging; it is not part of making a record and the
    UI is asked to keep it separate.
    """

    last_asr_ms: float = 0.0
    last_decision_ms: float = 0.0
    last_total_ms: float = 0.0
    engine: dict[str, str] = Field(default_factory=dict)
    calibration_warning: str | None = None
    notes: list[str] = Field(default_factory=list)


class SessionSnapshot(BaseModel):
    """The whole recoverable state of one call.

    Every mutating endpoint returns this, which is what lets a client confirm
    an action without re-running inference over the transcript.
    """

    model_config = ConfigDict(validate_assignment=True)

    session_id: str
    # Bumped ONLY by a committed operator write. This is what a review action
    # compares against, so a segment arriving mid-review cannot invalidate a
    # decision the operator already made about something unrelated.
    revision: int = 0
    # Bumped by everything, including appends. A polling or resuming client
    # uses this as its cursor; it is deliberately NOT the concurrency token.
    event_seq: int = 0
    created_at: str = Field(default_factory=utcnow)
    closed: bool = False

    segments: list[Segment] = Field(default_factory=list)
    accepted_record: dict[str, FieldValue] = Field(default_factory=dict)
    suggestions: list[Suggestion] = Field(default_factory=list)
    review_flags: list[ReviewFlag] = Field(default_factory=list)
    unresolved: list[UnresolvedItem] = Field(default_factory=list)

    narrative: NarrativeDraft = Field(default_factory=NarrativeDraft)
    published: list[PublishedSnapshot] = Field(default_factory=list)

    detected_language: str | None = None
    language_probability: float | None = None

    diagnostics: Diagnostics = Field(default_factory=Diagnostics)

    @property
    def pending(self) -> list[Suggestion]:
        return [s for s in self.suggestions if s.status is SuggestionStatus.PENDING]

    @property
    def open_flags(self) -> list[ReviewFlag]:
        return [f for f in self.review_flags if f.status == "open"]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class AppendSegmentRequest(BaseModel):
    """Append one finalized utterance.

    ``client_segment_id`` makes the append idempotent: a retry after a dropped
    response returns the segment that already exists instead of duplicating
    the utterance.
    """

    text: str | None = None
    speaker: Speaker = Speaker.CALLER
    language: str | None = None
    client_segment_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    whisper_size: str = "small"
    final: bool = True


class ReviewAction(BaseModel):
    """Accept, edit or reject one suggestion.

    Both fields matter and for different reasons. ``base_revision`` is what the
    operator was looking at, so the server can refuse to apply a decision made
    against a view that has since changed in a way that matters.
    ``operation_id`` is the client's own id for this action, so a double-click
    or a retry replays the original result instead of acting twice.
    """

    base_revision: int
    operation_id: str
    value: object = None
    reason: str | None = None
    note: str | None = None


class PublishRequest(BaseModel):
    base_revision: int
    operation_id: str


class NarrativeUpdate(BaseModel):
    text: str
    operation_id: str | None = None


class ConflictResponse(BaseModel):
    """Returned with 409 so a client can re-render rather than guess."""

    error: str
    reason: str
    current_revision: int
    suggestion_status: SuggestionStatus | None = None
    snapshot: SessionSnapshot
