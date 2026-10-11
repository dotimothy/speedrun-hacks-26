"""Rules protecting operator work, proven without a model.

Every test here runs against the session store alone. No Whisper, no Laya, no
network. That is the point: the guarantees issue #1 asks for are properties of
the state machine, so they should be checkable in milliseconds and should fail
loudly if someone makes the diff layer smarter.
"""

from __future__ import annotations

import pytest

from app.live.models import (
    ConflictOption,
    EvidenceMethod,
    EvidenceRef,
    Presence,
    Segment,
    Speaker,
    Suggestion,
    SuggestionStatus,
    UpdateKind,
)
from app.live.store import SessionStore, StaleAction


def segment(index: int, text: str, client_id: str | None = None) -> Segment:
    return Segment(
        id=f"seg-{index}",
        index=index,
        speaker=Speaker.CALLER,
        original_text=text,
        client_segment_id=client_id,
    )


def suggestion(
    sid: str,
    field: str,
    value: object,
    *,
    kind: UpdateKind = UpdateKind.ADDITION,
    previous: object = None,
    base: int = 0,
    segment_id: str = "seg-1",
) -> Suggestion:
    return Suggestion(
        id=sid,
        field=field,
        kind=kind,
        proposed_value=value,
        previous_value=previous,
        base_revision=base,
        evidence=[
            EvidenceRef(
                segment_id=segment_id,
                text=str(value),
                method=EvidenceMethod.RULE_VERBATIM,
            )
        ],
    )


@pytest.fixture
def store() -> SessionStore:
    return SessionStore()


# ---------------------------------------------------------------------------
# The worked example from the issue
# ---------------------------------------------------------------------------
class TestTheWorkedExample:
    """Oak and Third -> accept -> eastbound side -> "Sorry, Fourth, not Third"."""

    @pytest.fixture
    def session(self, store: SessionStore):
        snap = store.create("oak")
        store.commit_append(
            "oak", segment(1, "We are at Oak and Third"),
            [suggestion("sug-1", "location_text", "Oak and Third")], [], [],
        )
        return store, snap

    def test_a_segment_does_not_move_the_review_token(self, session):
        _, snap = session
        # An append bumps the event cursor but not the revision a review action
        # is checked against -- otherwise every accept would race a mid-call
        # utterance and the operator would re-confirm untouched fields.
        assert snap.revision == 0
        assert snap.event_seq == 1
        assert len(snap.pending) == 1

    def test_accepting_records_the_value_and_its_provenance(self, session):
        store, snap = session
        store.accept("oak", "sug-1", base_revision=0, operation_id="op-1")
        accepted = snap.accepted_record["location_text"]
        assert accepted.value == "Oak and Third"
        assert accepted.revision == 1
        assert accepted.source_segment_ids == ["seg-1"]
        assert len(accepted.history) == 1

    def test_an_unrelated_detail_leaves_accepted_fields_alone(self, session):
        store, snap = session
        store.accept("oak", "sug-1", base_revision=0, operation_id="op-1")
        store.commit_append(
            "oak", segment(2, "Eastbound side"),
            [suggestion("sug-2", "access_notes", "Eastbound side", base=1)], [], [],
        )
        accepted = snap.accepted_record["location_text"]
        # Byte-identical: nothing about the new detail touched this field.
        assert accepted.value == "Oak and Third"
        assert accepted.revision == 1
        assert len(accepted.history) == 1

    def test_a_correction_stays_pending_and_keeps_both_statements(self, session):
        store, snap = session
        store.accept("oak", "sug-1", base_revision=0, operation_id="op-1")
        store.commit_append(
            "oak", segment(3, "Sorry, Fourth, not Third"),
            [suggestion("sug-3", "location_text", "Oak and Fourth",
                        kind=UpdateKind.CORRECTION, previous="Oak and Third",
                        base=1, segment_id="seg-3")],
            [], [],
        )
        correction = next(s for s in snap.pending if s.kind is UpdateKind.CORRECTION)
        assert correction.status is SuggestionStatus.PENDING
        assert correction.previous_value == "Oak and Third"
        assert correction.proposed_value == "Oak and Fourth"
        # The accepted value is retained until somebody reviews the correction.
        assert snap.accepted_record["location_text"].value == "Oak and Third"


# ---------------------------------------------------------------------------
# Retries and stale decisions
# ---------------------------------------------------------------------------
class TestRetriesAndStaleness:
    @pytest.fixture
    def session(self, store: SessionStore):
        snap = store.create("retry")
        store.commit_append(
            "retry", segment(1, "at Oak and Third"),
            [suggestion("sug-1", "location_text", "Oak and Third"),
             suggestion("sug-2", "callback_number", "555 0147")],
            [], [],
        )
        return store, snap

    def test_the_same_operation_id_applies_once(self, session):
        store, snap = session
        store.accept("retry", "sug-1", base_revision=0, operation_id="op-1")
        revision_after_first = snap.revision
        store.accept("retry", "sug-1", base_revision=0, operation_id="op-1")
        assert snap.revision == revision_after_first
        assert len(snap.accepted_record["location_text"].history) == 1

    def test_a_replay_returns_the_original_result(self, session):
        store, _ = session
        first = store.accept("retry", "sug-1", base_revision=0, operation_id="op-1")
        replayed = store.accept("retry", "sug-1", base_revision=0, operation_id="op-1")
        assert replayed.revision == first.revision

    def test_a_different_operation_on_a_reviewed_suggestion_is_refused(self, session):
        store, _ = session
        store.accept("retry", "sug-1", base_revision=0, operation_id="op-1")
        with pytest.raises(StaleAction) as caught:
            store.accept("retry", "sug-1", base_revision=0, operation_id="op-2")
        assert caught.value.code == "suggestion_not_pending"

    def test_a_decision_made_against_an_older_view_is_refused(self, session):
        store, snap = session
        store.accept("retry", "sug-1", base_revision=0, operation_id="op-1")
        store.commit_append(
            "retry", segment(2, "actually Oak and Fourth"),
            [suggestion("sug-3", "location_text", "Oak and Fourth",
                        kind=UpdateKind.CORRECTION, previous="Oak and Third", base=1)],
            [], [],
        )
        # The operator is acting on a screen drawn before the accept landed.
        with pytest.raises(StaleAction) as caught:
            store.accept("retry", "sug-3", base_revision=0, operation_id="op-3")
        assert caught.value.code == "stale_revision"
        assert snap.accepted_record["location_text"].value == "Oak and Third"

    def test_an_unrelated_field_is_still_actionable_after_another_accept(self, session):
        store, snap = session
        store.accept("retry", "sug-1", base_revision=0, operation_id="op-1")
        # base_revision 0 is now behind the session, but nothing touched
        # callback_number, so this must still work.
        store.accept("retry", "sug-2", base_revision=0, operation_id="op-2")
        assert snap.accepted_record["callback_number"].value == "555 0147"

    def test_a_repeated_utterance_is_not_stored_twice(self, session):
        store, snap = session
        before = len(snap.segments)
        store.commit_append("retry", segment(9, "hello", client_id="c-9"), [], [], [])
        store.commit_append("retry", segment(9, "hello", client_id="c-9"), [], [], [])
        assert len(snap.segments) == before + 1


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------
class TestConflicts:
    @pytest.fixture
    def session(self, store: SessionStore):
        snap = store.create("conflict")
        store.commit_append(
            "conflict", segment(1, "four people, maybe five"),
            [Suggestion(
                id="sug-1", field="persons_involved", kind=UpdateKind.CONFLICT,
                base_revision=0, value_kind="unresolved",
                conflict_options=[
                    ConflictOption(option_id="a", value=4, statement="four people",
                                   segment_id="seg-1"),
                    ConflictOption(option_id="b", value=5, statement="maybe five",
                                   segment_id="seg-1"),
                ],
            )],
            [], [],
        )
        return store, snap

    def test_a_conflict_proposes_nothing(self, session):
        _, snap = session
        conflict = snap.pending[0]
        # Offering a value would be the machine picking a side of a
        # contradiction it just reported.
        assert conflict.proposed_value is None
        assert conflict.value_kind == "unresolved"
        assert len(conflict.conflict_options) == 2

    def test_a_conflict_cannot_be_accepted(self, session):
        store, snap = session
        with pytest.raises(StaleAction) as caught:
            store.accept("conflict", "sug-1", base_revision=0, operation_id="op-1")
        assert caught.value.code == "conflict_requires_resolution"
        assert "persons_involved" not in snap.accepted_record

    def test_resolving_records_the_choice_and_what_was_not_chosen(self, session):
        store, snap = session
        store.resolve("conflict", "sug-1", base_revision=0, operation_id="op-1",
                      option_id="b")
        accepted = snap.accepted_record["persons_involved"]
        assert accepted.value == 5
        assert "four people" in (accepted.note or "")

    def test_rejecting_a_correction_leaves_the_accepted_value(self, store: SessionStore):
        snap = store.create("reject")
        store.commit_append("reject", segment(1, "Oak and Third"),
                            [suggestion("sug-1", "location_text", "Oak and Third")], [], [])
        store.accept("reject", "sug-1", base_revision=0, operation_id="op-1")
        store.commit_append(
            "reject", segment(2, "no, Pine"),
            [suggestion("sug-2", "location_text", "Pine", kind=UpdateKind.CORRECTION,
                        previous="Oak and Third", base=1)], [], [],
        )
        store.reject("reject", "sug-2", base_revision=1, operation_id="op-2",
                     reason="caller was describing a landmark")
        assert snap.accepted_record["location_text"].value == "Oak and Third"
        assert snap.suggestions[-1].status is SuggestionStatus.REJECTED


# ---------------------------------------------------------------------------
# Session isolation and the unknown/negative distinction
# ---------------------------------------------------------------------------
class TestSessionsAndPresence:
    def test_a_second_session_starts_clean(self, store: SessionStore):
        first = store.create("call-1")
        store.commit_append("call-1", segment(1, "Oak and Third"),
                            [suggestion("sug-1", "location_text", "Oak and Third")], [], [])
        store.accept("call-1", "sug-1", base_revision=0, operation_id="op-1")
        store.publish("call-1", base_revision=first.revision, operation_id="op-2")

        second = store.create("call-2")
        assert second.revision == 0
        assert second.segments == []
        assert second.accepted_record == {}
        assert second.published == []

    def test_deleting_a_session_removes_it(self, store: SessionStore):
        store.create("gone")
        store.delete("gone")
        assert not store.exists("gone")

    def test_stated_absent_is_not_unknown(self, store: SessionStore):
        snap = store.create("presence")
        store.set_field_directly(
            "presence", "callback_number", value=None,
            presence=Presence.STATED_ABSENT, operation_id="op-1",
            note="caller says they have no phone",
        )
        accepted = snap.accepted_record["callback_number"]
        # Both hold value None. Only presence tells them apart, which is why
        # the field exists.
        assert accepted.value is None
        assert accepted.presence is Presence.STATED_ABSENT
        assert accepted.presence is not Presence.UNKNOWN


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------
class TestPublication:
    def test_nothing_is_published_until_a_human_publishes(self, store: SessionStore):
        snap = store.create("pub")
        store.commit_append("pub", segment(1, "Oak and Third"),
                            [suggestion("sug-1", "location_text", "Oak and Third")], [], [])
        store.accept("pub", "sug-1", base_revision=0, operation_id="op-1")
        assert snap.published == []

        store.publish("pub", base_revision=snap.revision, operation_id="op-2")
        assert len(snap.published) == 1
        assert snap.published[0].record["location_text"] == "Oak and Third"

    def test_publishing_is_allowed_with_fields_still_missing(self, store: SessionStore):
        snap = store.create("partial")
        _, published = store.publish("partial", base_revision=0, operation_id="op-1")
        # Completeness is explicitly not a gate; a dispatcher may need what is
        # known now more than a full record later.
        assert published.record == {}

    def test_a_second_publication_reports_only_what_changed(self, store: SessionStore):
        snap = store.create("delta")
        store.commit_append("delta", segment(1, "Oak and Third"),
                            [suggestion("sug-1", "location_text", "Oak and Third"),
                             suggestion("sug-2", "callback_number", "555 0147")], [], [])
        store.accept("delta", "sug-1", base_revision=0, operation_id="op-1")
        store.publish("delta", base_revision=snap.revision, operation_id="op-2")
        store.accept("delta", "sug-2", base_revision=snap.revision, operation_id="op-3")
        _, second = store.publish("delta", base_revision=snap.revision, operation_id="op-4")
        assert second.changed_fields == ["callback_number"]
        assert second.delta_from is not None

    def test_publishing_twice_with_one_operation_id_publishes_once(self, store: SessionStore):
        snap = store.create("idem")
        store.publish("idem", base_revision=0, operation_id="op-1")
        store.publish("idem", base_revision=0, operation_id="op-1")
        assert len(snap.published) == 1
