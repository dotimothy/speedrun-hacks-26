"""Session state: revisions, review actions, idempotency.

No model is called from this module and no I/O happens in it. Everything here
is a function of state the operator and the transcript produced, which is what
lets the rules protecting their work be tested in milliseconds without a GPU.

**Two counters, and they are not interchangeable.**

``revision`` is bumped only by a committed operator write -- accept, edit,
reject, resolve, publish. It is the token a review action is checked against.
``event_seq`` is bumped by everything, appends included, and is what a polling
or reconnecting client uses as a cursor.

Collapsing them into one would mean that a segment arriving while the operator
is reading the queue invalidates the accept they are about to click, because
the number they were shown has moved. They would then re-confirm untouched
fields all call, which is the exact friction this product exists to remove. An
append cannot overwrite operator work -- the diff never writes the record -- so
it must not invalidate an in-flight decision.

**Two protections, for two different failures.**

``operation_id`` stops an action happening twice: a double-click, a reconnect,
a client retrying after a dropped response. The stored result is replayed and
nothing is applied again.

``base_revision`` stops a stale decision overwriting a newer one. The check is
deliberately narrow -- only a change to the thing being acted on rejects the
action.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .models import (
    FieldRevision,
    FieldValue,
    Presence,
    PublishedSnapshot,
    SessionSnapshot,
    Suggestion,
    SuggestionStatus,
    UpdateKind,
    utcnow,
)


class SessionNotFound(KeyError):
    pass


@dataclass
class StaleAction(Exception):
    """An action decided against a view that has since moved."""

    reason: str
    code: str
    current_revision: int
    suggestion_status: SuggestionStatus | None = None


@dataclass
class _SessionState:
    snapshot: SessionSnapshot
    # operation_id -> the snapshot that operation produced. Replaying returns
    # the original result rather than applying anything a second time.
    operations: dict[str, SessionSnapshot] = field(default_factory=dict)
    client_segments: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def next_id(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}-{self.counters[prefix]}"


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, _SessionState] = {}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def create(self, session_id: str | None = None) -> SessionSnapshot:
        session_id = session_id or f"call-{uuid.uuid4().hex[:8]}"
        # Creating over an existing id replaces it outright. A second call
        # must start clean; carrying anything across would leak one caller's
        # confirmations into another caller's record.
        state = _SessionState(snapshot=SessionSnapshot(session_id=session_id))
        self._sessions[session_id] = state
        return state.snapshot

    def get(self, session_id: str) -> SessionSnapshot:
        return self._state(session_id).snapshot

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def exists(self, session_id: str) -> bool:
        return session_id in self._sessions

    def ids(self) -> list[str]:
        return list(self._sessions)

    def _state(self, session_id: str) -> _SessionState:
        state = self._sessions.get(session_id)
        if state is None:
            raise SessionNotFound(session_id)
        return state

    def next_id(self, session_id: str, prefix: str) -> str:
        return self._state(session_id).next_id(prefix)

    def seen_client_segment(self, session_id: str, client_segment_id: str | None) -> bool:
        if not client_segment_id:
            return False
        return client_segment_id in self._state(session_id).client_segments

    # ------------------------------------------------------------------
    # idempotency
    # ------------------------------------------------------------------
    def _replay(self, state: _SessionState, operation_id: str) -> SessionSnapshot | None:
        return state.operations.get(operation_id)

    def _record(self, state: _SessionState, operation_id: str | None) -> SessionSnapshot:
        snapshot = state.snapshot
        if operation_id:
            # Store a deep copy: the live snapshot keeps changing, and a
            # replay must return what the operation produced at the time.
            state.operations[operation_id] = snapshot.model_copy(deep=True)
        return snapshot

    # ------------------------------------------------------------------
    # appending -- bumps event_seq only
    # ------------------------------------------------------------------
    def commit_append(
        self,
        session_id: str,
        segment,
        suggestions: list[Suggestion],
        flags: list,
        unresolved: list,
        *,
        superseded: dict[str, str] | None = None,
        corroborated: dict[str, list[str]] | None = None,
        detected_language: str | None = None,
        language_probability: float | None = None,
        diagnostics=None,
    ) -> SessionSnapshot:
        """Add an utterance and whatever it implied. Inference happens elsewhere."""
        state = self._state(session_id)
        snapshot = state.snapshot

        if segment.client_segment_id:
            if segment.client_segment_id in state.client_segments:
                # The utterance is already here; a retry must not duplicate it.
                return snapshot
            state.client_segments[segment.client_segment_id] = segment.id

        snapshot.segments.append(segment)

        # A newer suggestion can retire an older pending one on the same field.
        for old_id, new_id in (superseded or {}).items():
            for existing in snapshot.suggestions:
                if existing.id == old_id and existing.status is SuggestionStatus.PENDING:
                    existing.status = SuggestionStatus.SUPERSEDED
                    existing.superseded_by = new_id

        # A later segment saying the same thing strengthens the existing
        # suggestion instead of stacking an identical one beside it.
        for suggestion_id, segment_ids in (corroborated or {}).items():
            for existing in snapshot.suggestions:
                if existing.id == suggestion_id:
                    for sid in segment_ids:
                        if sid not in existing.corroborating_segment_ids:
                            existing.corroborating_segment_ids.append(sid)

        snapshot.suggestions.extend(suggestions)
        snapshot.review_flags.extend(flags)
        snapshot.unresolved.extend(unresolved)

        if detected_language:
            snapshot.detected_language = detected_language
        if language_probability is not None:
            snapshot.language_probability = language_probability
        if diagnostics is not None:
            snapshot.diagnostics = diagnostics

        # event_seq only. See the module docstring: an append must not
        # invalidate a review decision made moments earlier.
        snapshot.event_seq += 1
        return snapshot

    # ------------------------------------------------------------------
    # review actions -- bump both counters
    # ------------------------------------------------------------------
    def _find(self, snapshot: SessionSnapshot, suggestion_id: str) -> Suggestion:
        for suggestion in snapshot.suggestions:
            if suggestion.id == suggestion_id:
                return suggestion
        raise KeyError(suggestion_id)

    def _check_actionable(
        self,
        snapshot: SessionSnapshot,
        suggestion: Suggestion,
        base_revision: int,
        *,
        allow_conflict: bool = False,
    ) -> None:
        if suggestion.status is not SuggestionStatus.PENDING:
            raise StaleAction(
                reason=f"suggestion is already {suggestion.status.value}",
                code="suggestion_not_pending",
                current_revision=snapshot.revision,
                suggestion_status=suggestion.status,
            )

        if suggestion.kind is UpdateKind.CONFLICT and not allow_conflict:
            # A conflict has no proposed value to accept. Accepting one would
            # mean the machine picked a side of a contradiction it reported.
            raise StaleAction(
                reason=(
                    "this is a conflict between statements and has no proposed value; "
                    "use resolve and name which statement is right"
                ),
                code="conflict_requires_resolution",
                current_revision=snapshot.revision,
                suggestion_status=suggestion.status,
            )

        accepted = snapshot.accepted_record.get(suggestion.field)
        if accepted is not None and accepted.revision > base_revision:
            raise StaleAction(
                reason=(
                    f"{suggestion.field} was accepted at revision {accepted.revision}, "
                    f"after the revision this action was based on ({base_revision})"
                ),
                code="stale_revision",
                current_revision=snapshot.revision,
                suggestion_status=suggestion.status,
            )

    def _write_field(
        self,
        snapshot: SessionSnapshot,
        *,
        field_name: str,
        value: object,
        presence: Presence,
        action: str,
        suggestion: Suggestion | None,
        operation_id: str | None,
        edited: bool,
        note: str | None,
    ) -> None:
        existing = snapshot.accepted_record.get(field_name)
        history = list(existing.history) if existing else []
        history.append(
            FieldRevision(
                revision=snapshot.revision,
                value=value,
                presence=presence,
                action=action,  # type: ignore[arg-type]
                suggestion_id=suggestion.id if suggestion else None,
                operation_id=operation_id,
                note=note,
            )
        )
        snapshot.accepted_record[field_name] = FieldValue(
            field=field_name,
            value=value,
            presence=presence,
            revision=snapshot.revision,
            source_segment_ids=(
                [e.segment_id for e in suggestion.evidence] if suggestion else []
            ),
            suggestion_id=suggestion.id if suggestion else None,
            edited=edited,
            note=note,
            history=history,
        )

    def _close_siblings(self, snapshot: SessionSnapshot, suggestion: Suggestion) -> None:
        """Retire the other side of a contradiction once one side is chosen.

        Marked superseded rather than deleted: the record of what the caller
        actually said has to survive the operator's decision about it.
        """
        for other_id in suggestion.conflicts_with:
            try:
                other = self._find(snapshot, other_id)
            except KeyError:
                continue
            if other.status is SuggestionStatus.PENDING:
                other.status = SuggestionStatus.SUPERSEDED
                other.superseded_by = suggestion.id

    def accept(
        self,
        session_id: str,
        suggestion_id: str,
        *,
        base_revision: int,
        operation_id: str,
        value: object = None,
        edited: bool = False,
        note: str | None = None,
    ) -> SessionSnapshot:
        state = self._state(session_id)
        replayed = self._replay(state, operation_id)
        if replayed is not None:
            return replayed

        snapshot = state.snapshot
        suggestion = self._find(snapshot, suggestion_id)
        self._check_actionable(snapshot, suggestion, base_revision)

        final_value = value if edited else suggestion.proposed_value
        snapshot.revision += 1
        snapshot.event_seq += 1

        suggestion.status = SuggestionStatus.EDITED if edited else SuggestionStatus.ACCEPTED
        suggestion.resolved_value = final_value
        suggestion.resolved_at = utcnow()
        if note:
            suggestion.note = note

        self._write_field(
            snapshot,
            field_name=suggestion.field,
            value=final_value,
            presence=suggestion.proposed_presence,
            action="edit" if edited else "accept",
            suggestion=suggestion,
            operation_id=operation_id,
            edited=edited,
            note=note,
        )
        self._close_siblings(snapshot, suggestion)
        return self._record(state, operation_id)

    def resolve(
        self,
        session_id: str,
        suggestion_id: str,
        *,
        base_revision: int,
        operation_id: str,
        option_id: str | None = None,
        value: object = None,
        note: str | None = None,
    ) -> SessionSnapshot:
        """Settle a conflict by naming which statement is right.

        Separate from ``accept`` on purpose: a conflict has no proposed value,
        so there is nothing to accept, and the operator has to say which of the
        caller's statements they are acting on.
        """
        state = self._state(session_id)
        replayed = self._replay(state, operation_id)
        if replayed is not None:
            return replayed

        snapshot = state.snapshot
        suggestion = self._find(snapshot, suggestion_id)
        self._check_actionable(snapshot, suggestion, base_revision, allow_conflict=True)

        chosen = value
        chosen_statement = None
        if option_id:
            for option in suggestion.conflict_options:
                if option.option_id == option_id:
                    chosen = option.value
                    chosen_statement = option.statement
                    break
            else:
                raise StaleAction(
                    reason=f"no option {option_id} on this conflict",
                    code="unknown_option",
                    current_revision=snapshot.revision,
                )

        snapshot.revision += 1
        snapshot.event_seq += 1
        suggestion.status = SuggestionStatus.EDITED
        suggestion.resolved_value = chosen
        suggestion.resolved_at = utcnow()
        rejected = [
            o.statement for o in suggestion.conflict_options if o.statement != chosen_statement
        ]
        suggestion.note = note or (
            "resolved; not chosen: " + "; ".join(rejected) if rejected else note
        )

        self._write_field(
            snapshot,
            field_name=suggestion.field,
            value=chosen,
            presence=Presence.STATED,
            action="resolve",
            suggestion=suggestion,
            operation_id=operation_id,
            edited=True,
            note=suggestion.note,
        )
        self._close_siblings(snapshot, suggestion)
        return self._record(state, operation_id)

    def reject(
        self,
        session_id: str,
        suggestion_id: str,
        *,
        base_revision: int,
        operation_id: str,
        reason: str | None = None,
    ) -> SessionSnapshot:
        state = self._state(session_id)
        replayed = self._replay(state, operation_id)
        if replayed is not None:
            return replayed

        snapshot = state.snapshot
        suggestion = self._find(snapshot, suggestion_id)
        self._check_actionable(snapshot, suggestion, base_revision, allow_conflict=True)

        snapshot.revision += 1
        snapshot.event_seq += 1
        suggestion.status = SuggestionStatus.REJECTED
        suggestion.resolved_at = utcnow()
        suggestion.note = reason or suggestion.note
        # The accepted record is untouched: rejecting a correction leaves the
        # previously accepted value exactly where it was.
        return self._record(state, operation_id)

    def set_field_directly(
        self,
        session_id: str,
        field_name: str,
        *,
        value: object,
        presence: Presence,
        operation_id: str,
        note: str | None = None,
    ) -> SessionSnapshot:
        """Operator entry with no suggestion behind it.

        This is how ``stated_absent`` gets recorded -- "the caller has no
        phone" is a fact no model will ever propose.
        """
        state = self._state(session_id)
        replayed = self._replay(state, operation_id)
        if replayed is not None:
            return replayed

        snapshot = state.snapshot
        snapshot.revision += 1
        snapshot.event_seq += 1
        self._write_field(
            snapshot,
            field_name=field_name,
            value=value,
            presence=presence,
            action="direct",
            suggestion=None,
            operation_id=operation_id,
            edited=True,
            note=note,
        )
        return self._record(state, operation_id)

    def acknowledge_flag(self, session_id: str, flag_id: str) -> SessionSnapshot:
        snapshot = self._state(session_id).snapshot
        for flag in snapshot.review_flags:
            if flag.id == flag_id and flag.status == "open":
                flag.status = "acknowledged"
                flag.acknowledged_at = utcnow()
                snapshot.event_seq += 1
                break
        return snapshot

    # ------------------------------------------------------------------
    # narrative and publication
    # ------------------------------------------------------------------
    def set_narrative(self, session_id: str, text: str) -> SessionSnapshot:
        snapshot = self._state(session_id).snapshot
        snapshot.narrative.edited = True
        snapshot.narrative.editor_text = text
        snapshot.event_seq += 1
        return snapshot

    def regenerate_narrative(self, session_id: str, text: str) -> None:
        """Refresh the generated draft without discarding an operator's edit."""
        snapshot = self._state(session_id).snapshot
        snapshot.narrative.text = text
        snapshot.narrative.generated_from_revision = snapshot.revision

    def publish(
        self, session_id: str, *, base_revision: int, operation_id: str
    ) -> tuple[SessionSnapshot, PublishedSnapshot]:
        state = self._state(session_id)
        replayed = self._replay(state, operation_id)
        if replayed is not None:
            return replayed, replayed.published[-1]

        snapshot = state.snapshot
        previous = snapshot.published[-1] if snapshot.published else None
        record = {name: value.value for name, value in snapshot.accepted_record.items()}
        changed = sorted(
            name
            for name, value in record.items()
            if previous is None or previous.record.get(name) != value
        )

        snapshot.revision += 1
        snapshot.event_seq += 1
        published = PublishedSnapshot(
            id=state.next_id("pub"),
            revision=snapshot.revision,
            record=record,
            narrative=snapshot.narrative.effective,
            unresolved=[item.text for item in snapshot.unresolved],
            open_flags=[flag.text for flag in snapshot.open_flags],
            delta_from=previous.revision if previous else None,
            changed_fields=changed,
        )
        snapshot.published.append(published)
        self._record(state, operation_id)
        return snapshot, published


store = SessionStore()
