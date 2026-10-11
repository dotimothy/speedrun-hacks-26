"""Rules for the one feature that acts on a caller without being asked to.

Autonomous mode speaks to a person in an emergency. Every one of these tests
is a thing it must not do.
"""

from __future__ import annotations

import pytest

from app.autoreply import SUPPORTED_LANGUAGES, decide
from app.schemas import Prediction, SuggestedAction
from app.tts import APPROVED


def preds(**values: object) -> list[Prediction]:
    return [Prediction(label=k, value=v, score=0.8) for k, v in values.items()]


def ask(action=SuggestedAction.REQUEST_MISSING_CRITICAL_FIELD, *, language="en",
        confirmed=None, already=None, **fields):
    return decide(
        suggested_action=action.value if isinstance(action, SuggestedAction) else action,
        predictions=preds(**fields),
        detected_language=language,
        confirmed=confirmed or {},
        already_asked=already or [],
    )


class TestItStaysQuietWhenItMust:
    def test_silent_during_an_escalation(self):
        # The single most important rule here: a weapon or a trapped caller
        # means a human takes over, not that the machine starts talking.
        reply, reason = ask(SuggestedAction.ESCALATE_TO_HUMAN_NOW, location_text=None)
        assert reply is None
        assert "human" in reason

    def test_silent_before_any_speech(self):
        reply, reason = ask(SuggestedAction.AWAIT_MORE_AUDIO, location_text=None)
        assert reply is None
        assert "nothing to reply to" in reason

    def test_silent_once_the_dispatcher_has_it(self):
        reply, _ = ask(SuggestedAction.READY_FOR_HUMAN_SUBMISSION, location_text=None)
        assert reply is None

    def test_silent_in_a_language_it_does_not_speak(self):
        # Asking a Portuguese speaker a question in Spanish because it is the
        # nearest installed voice would be worse than saying nothing.
        reply, reason = ask(language="pt", location_text=None)
        assert reply is None
        assert "language" in reason

    def test_silent_when_nothing_is_missing(self):
        reply, reason = ask(
            SuggestedAction.REQUEST_HUMAN_CONFIRMATION,
            incident_type="medical", location_text="1 A St", callback_number="555-0100",
            injuries="none", caller_safe="yes",
            chief_complaint="chest_pain", awake="yes", breathing="yes",
            patient_age="67 years old", medical_history="history of asthma",
            incident_timing="started earlier", access_notes="front entrance",
            medical_course="new",
            symptom_details=["Chest pain."], location_context=["1 A St"],
            people_details=["One patient."], scene_hazards=["No other hazards reported."],
        )
        assert reply is None
        assert "nothing is missing" in reason


class TestWhatItAsks:
    def test_asks_for_the_location_first(self):
        reply, _ = ask(location_text=None, callback_number=None, incident_type="unknown")
        assert reply is not None
        assert reply.target == "location_text"
        assert reply.phrase_id == "repeat_address_en"

    def test_moves_on_once_a_field_is_filled(self):
        reply, _ = ask(
            location_text="418 Alameda Street",
            incident_type="structure_fire",
            callback_number=None,
        )
        assert reply is not None and reply.target == "callback_number"

    def test_a_field_nothing_reported_counts_as_missing(self):
        # A prediction absent from the list is not evidence the field is fine.
        reply, _ = ask(location_text="418 Alameda Street")
        assert reply is not None and reply.target == "incident_type"

    def test_asks_in_the_caller_s_language(self):
        reply, _ = ask(language="es", location_text=None)
        assert reply is not None
        assert reply.phrase_id.endswith("_es")

    def test_does_not_ask_about_a_confirmed_field(self):
        # A human has already dealt with it; asking again wastes the call.
        reply, _ = ask(location_text=None, confirmed={"location_text": "418 Alameda Street"})
        assert reply is None or reply.target != "location_text"

    def test_never_repeats_a_question(self):
        reply, _ = ask(location_text=None, callback_number=None,
                       already=["repeat_address_en"])
        assert reply is not None
        assert reply.phrase_id != "repeat_address_en"

    def test_gives_up_rather_than_looping(self):
        asked = ["repeat_address_en", "what_happening_en", "ask_callback_en",
                 "ask_injuries_en", "ask_safe_en"]
        reply, _ = ask(location_text=None, callback_number=None,
                       incident_type="unknown", injuries="unknown",
                       caller_safe="unknown", already=asked)
        assert reply is None

    def test_interpreter_line_when_the_language_is_doubtful(self):
        reply, _ = ask(SuggestedAction.REQUEST_LANGUAGE_SUPPORT, location_text=None)
        assert reply is not None and reply.phrase_id.startswith("interpreter")


class TestItCanOnlySayApprovedThings:
    @pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
    def test_every_phrase_it_can_reach_exists_in_the_table(self, language):
        seen = set()
        for _ in range(8):
            reply, _ = ask(
                language=language, already=sorted(seen),
                location_text=None, callback_number=None, incident_type="unknown",
                injuries="unknown", caller_safe="unknown",
            )
            if reply is None:
                break
            assert reply.phrase_id in APPROVED, reply.phrase_id
            seen.add(reply.phrase_id)
        assert seen, "it should have asked something"

    def test_it_returns_an_id_and_never_text(self):
        reply, _ = ask(location_text=None)
        assert reply is not None
        # The dataclass carries no free-text field at all, which is what makes
        # "it cannot say anything unapproved" structural rather than a promise.
        assert not hasattr(reply, "text")

    # Reassurance lives in exactly one phrase per language, said once, as the
    # call is being closed. The product owner asked for it explicitly. It is
    # scoped this narrowly because the original rule was the right one for
    # every other phrase: a question or a holding line that implies something
    # has been dispatched is a promise this prototype cannot keep, and
    # autonomous mode plays those unprompted, mid-call, over and over.
    REASSURANCE = {"wrap_up_en", "wrap_up_es"}

    def test_no_question_or_holding_phrase_promises_help_is_coming(self):
        banned = ("on the way", "en camino", "dispatch", "help is coming",
                  "ayuda viene", "sending", "enviando", "minutes", "minutos")
        for phrase_id, (_, text) in APPROVED.items():
            if phrase_id in self.REASSURANCE:
                continue
            lowered = text.lower()
            for word in banned:
                assert word not in lowered, f"{phrase_id}: {text}"

    def test_no_phrase_commits_to_a_time_or_a_unit(self):
        """The closing phrase may reassure. It still may not promise.

        "Help is on the way" is a statement about a dispatcher's decision.
        "Four minutes" or "an ambulance" would be a claim about a resource
        this system never allocated, which is the line that does not move.
        """
        banned = ("minutes", "minutos", "ambulance", "ambulancia", "engine",
                  "fire truck", "bomberos", "police", "polic", "units",
                  "unidades", "eta")
        for phrase_id, (_, text) in APPROVED.items():
            lowered = text.lower()
            for word in banned:
                # ETA is an abbreviation, not the ending of Spanish "quieta".
                import re
                found = bool(re.search(r"\beta\b", lowered)) if word == "eta" else word in lowered
                assert not found, f"{phrase_id}: {text}"

    def test_the_reassurance_is_only_in_the_closing_phrase(self):
        """One phrase per language, so it cannot be said at an arbitrary time."""
        reassuring = {
            pid for pid, (_, text) in APPROVED.items()
            if "on the way" in text.lower() or "en camino" in text.lower()
        }
        assert reassuring == self.REASSURANCE
