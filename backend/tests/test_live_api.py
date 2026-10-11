"""The v2 HTTP surface, driven end to end.

Laya is stubbed out in most of these so the suite stays fast and runs without
a GPU. The deterministic rule extractor is left real, because it is pure regex
and it is what actually produces the locations and phone numbers the worked
example turns on.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.decision.base import EngineResult
from app.live import router as live_router
from app.live.store import store
from app.main import app


@pytest.fixture(autouse=True)
def quiet_laya(monkeypatch):
    """Stub Laya: these tests are about state transitions, not inference."""
    class _Stub:
        name = "laya-stub"
        is_local = True

        def ready(self):
            return True

        def describe(self):
            return {"name": self.name, "is_local": True, "inference_location": "stub"}

        def extract(self, original, english, language):
            return EngineResult(answers={}, engine="laya-stub", model="stub", notes=[])

    monkeypatch.setattr(live_router, "get_engine", lambda name=None: _Stub())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def session(client: TestClient) -> str:
    for existing in list(store.ids()):
        store.delete(existing)
    response = client.post("/api/v2/sessions", json={"session_id": "t-oak"})
    assert response.status_code == 201
    return "t-oak"


def say(client: TestClient, session: str, text: str, **kwargs) -> dict:
    response = client.post(
        f"/api/v2/sessions/{session}/segments",
        json={"text": text, "speaker": "caller", **kwargs},
    )
    assert response.status_code == 200, response.text
    return response.json()


def find(snapshot: dict, field: str) -> dict | None:
    for suggestion in snapshot["suggestions"]:
        if suggestion["field"] == field and suggestion["status"] == "pending":
            return suggestion
    return None


class TestTheWorkedExampleOverHttp:
    def test_updates_arrive_before_the_call_ends(self, client, session):
        first = say(client, session, "We are at Oak and Third")
        # A suggestion exists while the call is still going, and nothing has
        # been written to the record.
        assert first["new_suggestion_ids"]
        assert first["snapshot"]["revision"] == 0
        assert first["snapshot"]["event_seq"] == 1
        assert first["snapshot"]["accepted_record"] == {}

    def test_the_full_sequence(self, client, session):
        snapshot = say(client, session, "We are at Oak and Third")["snapshot"]
        location = find(snapshot, "location_text")
        assert location["kind"] == "addition"

        accepted = client.post(
            f"/api/v2/sessions/{session}/suggestions/{location['id']}/accept",
            json={"base_revision": 0, "operation_id": "op-accept"},
        ).json()
        assert accepted["inference_ran"] is False
        record = accepted["snapshot"]["accepted_record"]
        assert record["location_text"]["value"] == "Oak and Third"

        # An unrelated detail must not disturb the accepted field.
        after = say(client, session, "We are on the eastbound side")["snapshot"]
        assert after["accepted_record"]["location_text"]["value"] == "Oak and Third"
        assert after["accepted_record"]["location_text"]["revision"] == 1

        # The correction stays pending; the accepted value stands.
        corrected = say(client, session, "Sorry, Fourth, not Third")["snapshot"]
        correction = find(corrected, "location_text")
        assert correction["kind"] == "correction"
        assert correction["proposed_value"] == "Oak and Fourth"
        assert correction["previous_value"] == "Oak and Third"
        assert corrected["accepted_record"]["location_text"]["value"] == "Oak and Third"


class TestReviewActionsRunNoModel:
    def test_every_review_path_works_with_the_engines_disabled(
        self, client, session, monkeypatch
    ):
        snapshot = say(client, session, "We are at Oak and Third")["snapshot"]
        suggestion_id = find(snapshot, "location_text")["id"]

        def explode(*args, **kwargs):
            raise AssertionError("a review action must never run inference")

        monkeypatch.setattr(live_router, "get_engine", explode)
        monkeypatch.setattr(live_router.whisper_engine, "transcribe_and_translate", explode)

        accepted = client.post(
            f"/api/v2/sessions/{session}/suggestions/{suggestion_id}/accept",
            json={"base_revision": 0, "operation_id": "op-1"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["inference_ran"] is False

        assert client.get(f"/api/v2/sessions/{session}").status_code == 200
        assert client.post(
            f"/api/v2/sessions/{session}/publish",
            json={"base_revision": 1, "operation_id": "op-2"},
        ).status_code == 201


class TestConcurrencyOverHttp:
    def test_a_replayed_operation_applies_once(self, client, session):
        snapshot = say(client, session, "call me on 555 0147")["snapshot"]
        suggestion_id = find(snapshot, "callback_number")["id"]
        body = {"base_revision": 0, "operation_id": "op-dup"}
        first = client.post(
            f"/api/v2/sessions/{session}/suggestions/{suggestion_id}/accept", json=body
        ).json()
        second = client.post(
            f"/api/v2/sessions/{session}/suggestions/{suggestion_id}/accept", json=body
        ).json()
        assert first["snapshot"]["revision"] == second["snapshot"]["revision"]
        history = second["snapshot"]["accepted_record"]["callback_number"]["history"]
        assert len(history) == 1

    def test_a_stale_action_is_refused_with_the_current_state(self, client, session):
        snapshot = say(client, session, "at Oak and Third, call 555 0147")["snapshot"]
        location_id = find(snapshot, "location_text")["id"]
        client.post(
            f"/api/v2/sessions/{session}/suggestions/{location_id}/accept",
            json={"base_revision": 0, "operation_id": "op-1"},
        )
        said = say(client, session, "actually Pine and Main")["snapshot"]
        correction_id = find(said, "location_text")["id"]

        response = client.post(
            f"/api/v2/sessions/{session}/suggestions/{correction_id}/accept",
            json={"base_revision": 0, "operation_id": "op-2"},
        )
        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "stale_revision"
        assert body["applied"] is False
        # The 409 carries authoritative state so the client re-renders instead
        # of issuing a follow-up GET that could race again.
        assert body["snapshot"]["accepted_record"]["location_text"]["value"] == "Oak and Third"

    def test_a_repeated_utterance_is_not_stored_twice(self, client, session):
        say(client, session, "at Oak and Third", client_segment_id="c-1")
        repeat = say(client, session, "at Oak and Third", client_segment_id="c-1")
        assert repeat["idempotent_replay"] is True
        assert len(repeat["snapshot"]["segments"]) == 1


class TestPublication:
    def test_the_receiving_view_is_empty_until_a_human_publishes(self, client, session):
        snapshot = say(client, session, "at Oak and Third")["snapshot"]
        suggestion_id = find(snapshot, "location_text")["id"]
        client.post(
            f"/api/v2/sessions/{session}/suggestions/{suggestion_id}/accept",
            json={"base_revision": 0, "operation_id": "op-1"},
        )
        assert client.get("/api/v2/dispatch/inbox").json()["items"] == []

        published = client.post(
            f"/api/v2/sessions/{session}/publish",
            json={"base_revision": 1, "operation_id": "op-2"},
        )
        assert published.status_code == 201
        assert published.json()["routed"] == "no"

        inbox = client.get("/api/v2/dispatch/inbox").json()["items"]
        assert len(inbox) == 1
        assert inbox[0]["record"]["location_text"] == "Oak and Third"

    def test_the_narrative_names_what_is_not_stated(self, client, session):
        say(client, session, "at Oak and Third")
        snapshot = client.get(f"/api/v2/sessions/{session}").json()
        text = snapshot["narrative"]["text"]
        assert snapshot["narrative"]["method"] == "template"
        # A gap is reported as a gap rather than omitted, and the draft says
        # plainly that it is a draft.
        assert "not stated" in text
        assert "Draft assembled from accepted fields only" in text


class TestSessionIsolation:
    def test_a_second_session_shares_nothing(self, client, session):
        snapshot = say(client, session, "at Oak and Third")["snapshot"]
        suggestion_id = find(snapshot, "location_text")["id"]
        client.post(
            f"/api/v2/sessions/{session}/suggestions/{suggestion_id}/accept",
            json={"base_revision": 0, "operation_id": "op-1"},
        )
        client.post("/api/v2/sessions", json={"session_id": "t-other"})
        other = client.get("/api/v2/sessions/t-other").json()
        assert other["revision"] == 0
        assert other["accepted_record"] == {}
        assert other["segments"] == []

    def test_an_unknown_session_is_a_404(self, client):
        assert client.get("/api/v2/sessions/nope").status_code == 404

    def test_stated_absent_is_recorded_distinctly(self, client, session):
        client.post(
            f"/api/v2/sessions/{session}/fields/callback_number",
            json={"value": None, "presence": "stated_absent",
                  "operation_id": "op-1", "note": "caller has no phone"},
        )
        record = client.get(f"/api/v2/sessions/{session}").json()["accepted_record"]
        assert record["callback_number"]["presence"] == "stated_absent"
        assert record["callback_number"]["value"] is None


def test_lowercase_intersection_creates_location_proposal(client, session):
    snapshot = say(client, session, "We are at oak and third.")["snapshot"]
    proposal = find(snapshot, "location_text")
    assert proposal is not None
    assert proposal["proposed_value"] == "oak and third"
    assert proposal["evidence"][0]["text"].lower() == "oak and third"


def test_lowercase_intersection_does_not_turn_symptoms_into_location():
    from app.decision.rules import find_all_locations
    assert find_all_locations("He is coughing and bleeding") == []
    assert find_all_locations("We are with john and mary") == []


def test_full_replacement_intersection_precedes_ordinal_substitution(client, session):
    snapshot = say(client, session, "we are at oak and third.")["snapshot"]
    suggestion = find(snapshot, "location_text")
    client.post(f"/api/v2/sessions/{session}/suggestions/{suggestion['id']}/accept",
                json={"base_revision": 0, "operation_id": "accept-original"})
    updated = say(client, session, "sorry, fourth and third")["snapshot"]
    assert find(updated, "location_text")["proposed_value"] == "fourth and third"
    assert updated["accepted_record"]["location_text"]["value"] == "oak and third"


def test_accepted_correction_does_not_recur_on_unrelated_utterances(client, session):
    first = say(client, session, "We're at Oak and Third")["snapshot"]
    location = find(first, "location_text")
    client.post(f"/api/v2/sessions/{session}/suggestions/{location['id']}/accept",
                json={"base_revision": 0, "operation_id": "accept-original"})
    corrected = say(client, session, "Sorry, it's Fourth and Third")["snapshot"]
    location = find(corrected, "location_text")
    assert location["proposed_value"] == "Fourth and Third"
    client.post(f"/api/v2/sessions/{session}/suggestions/{location['id']}/accept",
                json={"base_revision": 1, "operation_id": "accept-correction"})
    for text in ["My roommate burned his hand. He's awake and talking",
                 "I don't know if anyone is still upstairs.",
                 "There's a strong smell, but I can't tell if it's gas or just smoke",
                 "You can call me back at 415-555-0136"]:
        snapshot = say(client, session, text)["snapshot"]
        assert find(snapshot, "location_text") is None
        assert snapshot["accepted_record"]["location_text"]["value"] == "Fourth and Third"
    assert find(snapshot, "callback_number")["proposed_value"] == "415-555-0136"
