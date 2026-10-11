"""Turn raw engine answers into predictions a dispatcher can act on.

This is the only module allowed to decide that an answer is not good enough to
show as a fact. Keeping that policy here, out of the engines, is what makes it
testable without loading a model -- and the policy, not the model, is what the
product's credibility rests on.

Why the scores are built rather than read
-----------------------------------------
Every self-reported confidence available on this stack was measured and found
useless:

* A generative model asked for a 0-1 score returned exactly ``1.0`` every time.
* Token logprobs under constrained JSON decoding measure grammar friction, not
  belief: the opening ``{"`` scored p ~ 0 because the model wanted to begin
  "Here is the...".
* Self-consistency over 7 samples at temperature 1.0 agreed 7/7 on every case,
  including the deliberately ambiguous ones. It costs ~800 ms and buys nothing.
* Laya reports real probabilities, but its own documentation says the shipped
  checkpoints are over-confident and the temperatures are invalid for some
  option counts, and loading one prints a warning saying so.

What is left is agreement between independent methods, which is measurable:

1. Laya reading the caller's own words.
2. Laya reading Whisper's English translation -- a different checkpoint on a
   different rendering of the same sentence.
3. A deterministic regex/keyword extractor that shares no machinery with Laya.
4. Whisper's own per-word probability, the one signal here that genuinely
   varies with input quality.

Agreement raises the score, disagreement lowers it and is surfaced, and
anything that lands under the abstention threshold is reported as ``unknown``
rather than guessed. That makes ``unknown`` mean "we were not sure enough",
which is a claim we can actually stand behind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from .config import settings
from .scenarios import DETAIL_LABELS
from .decision.base import EngineResult, FieldAnswer
from .decision.questions import EVIDENCE_TERMS
from .decision.scenario_tasks import DECISIONS
from .evidence import locate_term, resolve as resolve_evidence
from .schemas import (
    ChiefComplaint,
    EvidenceSpan,
    FireExtent,
    Hazard,
    IncidentForm,
    IncidentType,
    Injuries,
    Occupancy,
    Prediction,
    Tristate,
    FireOccupants, MedicalCourse, RoadBlockage, CrimePresence, CrimeActivity,
    GasLocation, GasExposure, WaterVisibility, WaterMotion,
)

CHOICE_FIELDS = (
    "incident_type",
    "injuries",
    "occupancy",
    "fire_extent",
    "chief_complaint",
    "awake",
    "breathing",
    *DECISIONS,
)
NOUL_FIELDS = ("weapons_present",)
RULE_FIELDS = ("location_text", "callback_number", "persons_involved", "caller_name", *DETAIL_LABELS)
# A quoted phrase is the value. Laya abstaining does not hide "he is awake".
SPOKEN_FIELDS = {
    "injuries",
    "occupancy",
    "fire_extent",
    "chief_complaint",
    "awake",
    "breathing",
}
FIRE_NATURES = {IncidentType.STRUCTURE_FIRE, IncidentType.GAS_LEAK}

# A noul field that the deterministic extractor can also speak to.
NOUL_RULE_SOURCE = {"weapons_present": "hazard_weapon"}

# A pattern match is strong evidence but not proof: the regex can find a
# plausible street fragment in a sentence that was not an address.
RULE_FIELD_SCORE = {
    "callback_number": 0.95,
    "location_text": 0.8,
    "persons_involved": 0.85,
    "caller_name": 0.9,
}

_ENUMS = {
    "incident_type": IncidentType,
    "injuries": Injuries,
    "occupancy": Occupancy,
    "fire_extent": FireExtent,
    "chief_complaint": ChiefComplaint,
    "awake": Tristate,
    "breathing": Tristate,
    "fire_occupants": FireOccupants,
    "medical_course": MedicalCourse,
    "collision_entrapment": Tristate,
    "collision_road_blockage": RoadBlockage,
    "crime_presence": CrimePresence,
    "crime_activity": CrimeActivity,
    "gas_location": GasLocation,
    "gas_exposure": GasExposure,
    "water_visibility": WaterVisibility,
    "water_motion": WaterMotion,
}


@dataclass
class Resolution:
    predictions: list[Prediction]
    form: IncidentForm
    notes: list[str] = field(default_factory=list)


def _tristate(p_true: float) -> Tristate:
    """Map a probability onto yes / no / unknown.

    The band between the thresholds is the whole point: a 0.45 is not a "no",
    it is an absence of knowledge, and collapsing it into one would be the
    exact failure this product claims to avoid.
    """
    if p_true >= settings.noul_high:
        return Tristate.YES
    if p_true <= settings.noul_low:
        return Tristate.NO
    return Tristate.UNKNOWN


def _asr_factor(asr_confidence: float | None) -> tuple[float, str]:
    """Weight by how well the audio was heard.

    On the transcript-input path there is no audio, so there is no ASR
    confidence. That returns a factor of 1.0 and says it is not applicable --
    it never invents a number.
    """
    if asr_confidence is None:
        return 1.0, "not_applicable"
    # Whisper word probabilities sit high even on poor audio, so compress the
    # useful range rather than multiplying a 0.72 straight into the score.
    return 0.6 + 0.4 * max(0.0, min(1.0, asr_confidence)), "measured"


def _evidence_for(label: str, quote: str | None, original: str, english: str):
    """Find a span to highlight.

    For a rule-extracted field the quote is the matched text itself, so this is
    a lookup. For a Laya-answered field there is no quote -- Laya scores
    options, it does not point at text -- so a domain term is located instead.
    A miss means "nothing to highlight", never "the answer is wrong": Laya made
    no claim about the transcript, so there is no claim to falsify.
    """
    if label in DECISIONS and not quote:
        # Prefer the latest relevant sentence for scenario decisions. A
        # correction must not be displayed beside the older statement.
        for text, source in ((original, "original"), (english, "english")):
            matches = [m for m in re.finditer(r"[^.!?\n]+[.!?]?", text or "")
                       if any(term in m.group().lower() for term in EVIDENCE_TERMS[label])]
            if matches:
                match = matches[-1]
                start = match.start() + len(match.group()) - len(match.group().lstrip())
                return EvidenceSpan(text=match.group().strip(), start=start, end=start + len(match.group().strip()), source=source)
        return None
    if quote:
        # A model-supplied quote is held to the strict standard: it must be
        # locatable as a quote, minimum length included, because its whole
        # purpose is to be a verifiable claim.
        span = resolve_evidence(quote, original, english)
        if span is not None:
            return span
        # A quote from the deterministic extractor is a different thing: the
        # term was already matched verbatim, so the minimum-length guard does
        # not apply to it. Anchor it and widen to the sentence. Without this a
        # Spanish call whose weapon word was matched in the English
        # translation ("knife", 5 characters) could not anchor at all.
        for text, source in ((original, "original"), (english, "english")):
            if not text:
                continue
            span = locate_term(quote, text, source)
            if span is not None:
                return span
        return None
    for term in EVIDENCE_TERMS.get(label, ()):  # ordered, most specific first
        for text, source in ((original, "original"), (english, "english")):
            if not text:
                continue
            span = locate_term(term, text, source)
            if span is not None:
                return span
    return None


def _gate_on_evidence(
    span, score: float, breakdown: dict[str, object]
) -> tuple[float, dict[str, object]]:
    """Cap a claim that nothing independent supports.

    The product's promise is that every field is evidence-backed. A value with
    no span to point at and no agreement from a second checker has nothing
    behind it but one model's probability, and that probability has been
    measured to be over-confident. Capping it below the abstention threshold
    turns it into ``unknown``, which is the honest report.

    Agreement from the other language pass or from the deterministic extractor
    counts as corroboration, so a well-supported field is untouched.
    """
    corroborated = (
        breakdown.get("cross_check") == "agree" or breakdown.get("rule_check") == "agree"
    )
    if span is None and not corroborated:
        capped = min(score, settings.uncorroborated_cap)
        if capped < score:
            breakdown["uncorroborated"] = (
                "no evidence span located and no independent agreement; "
                f"score capped at {settings.uncorroborated_cap}"
            )
            breakdown["final"] = round(capped, 4)
        return capped, breakdown
    return score, breakdown


def _combine(
    label: str,
    primary: FieldAnswer,
    cross: FieldAnswer | None,
    rule: FieldAnswer | None,
    primary_value: object,
    cross_value: object,
    rule_value: object,
    asr_factor: float,
    asr_source: str,
) -> tuple[object, float, bool, object, dict[str, object]]:
    """Fold the three opinions into one value, score and conflict flag."""
    # An engine that reports no confidence is not an engine reporting zero.
    # OpenAI is asked only for the typed answer, so it arrives here with no
    # number and gets a neutral prior that corroboration then moves.
    if primary.probability is None:
        score = settings.typed_answer_prior
        reported = "none (typed answer, neutral prior applied)"
    else:
        score = float(primary.probability)
        reported = round(score, 4)
    breakdown: dict[str, object] = {
        "laya_original": reported,
        "asr_factor": round(asr_factor, 3),
        "asr_confidence": asr_source,
    }
    conflict = False
    alternative: object = None
    value = primary_value

    if cross is not None:
        cross_p = float(cross.probability or 0.0)
        breakdown["laya_english"] = round(cross_p, 4)
        if cross_value == primary_value:
            # Two checkpoints, two renderings, same answer.
            score = (score + cross_p) / 2.0
            breakdown["cross_check"] = "agree"
        else:
            conflict = True
            alternative = cross_value
            score = min(score, cross_p) * settings.conflict_penalty
            breakdown["cross_check"] = "disagree"
            breakdown["cross_value"] = cross_value
    else:
        breakdown["cross_check"] = "unavailable"

    if rule is not None:
        if rule_value == primary_value:
            score = min(1.0, score * 1.1)
            breakdown["rule_check"] = "agree"
        else:
            # The deterministic extractor disagreeing is a real warning, but it
            # is keyword-shallow, so it dampens rather than overrules.
            score *= 0.6
            conflict = True
            breakdown["rule_check"] = "disagree"
            breakdown["rule_value"] = rule_value
            if alternative is None:
                alternative = rule_value
    else:
        breakdown["rule_check"] = "no_opinion"

    score *= asr_factor
    score = max(0.0, min(1.0, score))
    breakdown["final"] = round(score, 4)
    return value, score, conflict, alternative, breakdown


def resolve(
    laya: EngineResult | None,
    rules: EngineResult,
    original: str,
    english: str,
    asr_confidence: float | None = None,
    incident_context: str | None = None,
) -> Resolution:
    predictions: list[Prediction] = []
    form = IncidentForm()
    notes: list[str] = []
    asr_factor, asr_source = _asr_factor(asr_confidence)

    laya_answers = laya.answers if laya else {}
    laya_cross = laya.cross if laya else {}
    rule_answers = rules.answers

    # ---------------------------------------------------------------- choice
    for label in CHOICE_FIELDS:
        primary = laya_answers.get(label)
        enum = _ENUMS[label]
        if primary is None:
            rule = rule_answers.get(label)
            allowed = {item.value for item in enum}
            if rule is not None and rule.value in allowed and (
                label in SPOKEN_FIELDS or label == "incident_type"
            ):
                predictions.append(
                    Prediction(
                        label=label,
                        value=rule.value,
                        score=settings.choice_min_confidence,
                        decided_on="rules",
                        breakdown={"spoken": rule.value},
                    )
                )
                setattr(form, label, enum(rule.value))
                continue
            predictions.append(
                Prediction(
                    label=label,
                    value=enum.UNKNOWN.value,
                    score=0.0,
                    breakdown={"reason": "no engine answered this field"},
                )
            )
            continue

        cross = laya_cross.get(label)
        rule = rule_answers.get(label)
        value, score, conflict, alternative, breakdown = _combine(
            label, primary, cross, rule,
            primary.value, cross.value if cross else None, rule.value if rule else None,
            asr_factor, asr_source,
        )

        span = _evidence_for(label, None, original, english)
        score, breakdown = _gate_on_evidence(span, score, breakdown)

        abstained = score < settings.choice_min_confidence
        # A dedicated decision is a model interpretation with located context,
        # not a free-form note. Invalid/unsupported answers stay unknown even
        # if a provider returns a large probability.
        if label in DECISIONS and (span is None or value not in {v.value for v in enum}):
            abstained = True
            breakdown["unsupported"] = "no located context or invalid scenario choice"
        if label == "collision_entrapment" and span is not None and re.search(
            r"(?:do not know|don't know|unsure|not sure|no s[eé]).{0,80}(?:get out|trapped|salir|atrap)", span.text, re.I
        ):
            abstained = True
            breakdown["unsupported"] = "caller explicitly says entrapment is not known"
        if abstained:
            breakdown["abstained"] = (f"score < {settings.choice_min_confidence}"
                                      if score < settings.choice_min_confidence else breakdown.get("unsupported"))
            value = enum.UNKNOWN.value

        if (
            rule is not None
            and label in SPOKEN_FIELDS
            and rule.value in {item.value for item in enum}
        ):
            value = rule.value
            score = max(score, settings.choice_min_confidence)
            conflict = False
            alternative = None
            breakdown.pop("abstained", None)
            breakdown["spoken"] = rule.value

        predictions.append(
            Prediction(
                label=label,
                value=value,
                score=round(score, 3),
                evidence=span,
                conflict=conflict,
                probabilities=primary.probabilities,
                raw_probability=primary.probability,
                decided_on="original",
                alternative=alternative,
                breakdown=breakdown,
            )
        )
        setattr(form, label, enum(value))

    # ------------------------------------------------------------------ noul
    for label in NOUL_FIELDS:
        primary = laya_answers.get(label)
        if primary is None:
            predictions.append(Prediction(label=label, value=Tristate.UNKNOWN.value, score=0.0))
            continue

        cross = laya_cross.get(label)
        primary_tri = _tristate(float(primary.value))
        cross_tri = _tristate(float(cross.value)) if cross else None

        # The deterministic extractor has an opinion on weapons: it looks for
        # weapon words. Without this, a Spanish call saying "tiene un
        # cuchillo" scored weapons_present=unknown because the two Laya passes
        # disagreed and nothing else was consulted.
        rule_tri = None
        rule_hit = rule_answers.get(NOUL_RULE_SOURCE.get(label, ""))
        if rule_hit is not None:
            rule_tri = Tristate.YES.value

        value, score, conflict, alternative, breakdown = _combine(
            label, primary, cross, rule_hit,
            primary_tri.value, cross_tri.value if cross_tri else None, rule_tri,
            asr_factor, asr_source,
        )
        breakdown["p_true"] = round(float(primary.value), 4)
        breakdown["thresholds"] = f"yes>={settings.noul_high} no<={settings.noul_low}"

        # A tri-state whose two passes landed in different bands is exactly
        # what "unknown" is for. Reporting the primary's band here would hide a
        # real disagreement behind a confident-looking yes or no.
        if conflict:
            value = Tristate.UNKNOWN.value
            breakdown["forced_unknown"] = "passes disagreed after thresholding"

        span = _evidence_for(label, rule_hit.quote if rule_hit else None, original, english)
        score, breakdown = _gate_on_evidence(span, score, breakdown)

        # A weapon word in the transcript is positive evidence, and it should
        # not be buried by two model passes disagreeing about it. Report "yes"
        # with the quote attached and let the escalation rule see it.
        if rule_tri == Tristate.YES.value and span is not None and value != Tristate.YES.value:
            value = Tristate.YES.value
            score = max(score, 0.6)
            breakdown["rule_override"] = (
                "weapon term found verbatim in the transcript; reported as yes"
            )

        predictions.append(
            Prediction(
                label=label,
                value=value,
                score=round(score, 3),
                evidence=span,
                conflict=conflict,
                probabilities=primary.probabilities,
                raw_probability=primary.probability,
                alternative=alternative,
                breakdown=breakdown,
            )
        )
        setattr(form, label, Tristate(value))

    # ------------------------------------------------- composed: caller_safe
    # Built from two positive-evidence questions rather than one question
    # about the absence of danger. See questions.py for why: the single
    # question made every call escalate.
    trapped = laya_answers.get("caller_trapped")
    evacuated = laya_answers.get("caller_evacuated")
    if trapped is not None or evacuated is not None:
        p_trapped = float(trapped.value) if trapped else 0.0
        p_evac = float(evacuated.value) if evacuated else 0.0
        trapped_tri = _tristate(p_trapped)
        evac_tri = _tristate(p_evac)

        if trapped_tri is Tristate.YES and evac_tri is not Tristate.YES:
            safe_value, safe_score, why = Tristate.NO.value, p_trapped, "caller states they are trapped"
        elif evac_tri is Tristate.YES and trapped_tri is not Tristate.YES:
            safe_value, safe_score, why = Tristate.YES.value, p_evac, "caller states they got clear"
        elif trapped_tri is Tristate.YES and evac_tri is Tristate.YES:
            # Both asserted: contradictory, so we do not know.
            safe_value, safe_score, why = Tristate.UNKNOWN.value, 0.0, "both trapped and clear asserted"
        else:
            safe_value, safe_score, why = Tristate.UNKNOWN.value, 0.0, "the call never says either way"

        # Cross-check both components against the English pass.
        cross_trapped = laya_cross.get("caller_trapped")
        cross_evac = laya_cross.get("caller_evacuated")
        safe_conflict = False
        if cross_trapped is not None and _tristate(float(cross_trapped.value)) is not trapped_tri:
            safe_conflict = True
        if cross_evac is not None and _tristate(float(cross_evac.value)) is not evac_tri:
            safe_conflict = True
        if safe_conflict:
            safe_value = Tristate.UNKNOWN.value
            safe_score = 0.0
            why += "; the two language passes disagreed"

        safe_score = max(0.0, min(1.0, safe_score * asr_factor))
        predictions.append(
            Prediction(
                label="caller_safe",
                value=safe_value,
                score=round(safe_score, 3),
                evidence=_evidence_for(
                    "caller_trapped" if safe_value == Tristate.NO.value else "caller_evacuated",
                    None, original, english,
                ),
                conflict=safe_conflict,
                probabilities={"trapped": round(p_trapped, 4), "evacuated": round(p_evac, 4)},
                breakdown={
                    "composed_from": ["caller_trapped", "caller_evacuated"],
                    "p_trapped": round(p_trapped, 4),
                    "p_evacuated": round(p_evac, 4),
                    "reason": why,
                    "asr_factor": round(asr_factor, 3),
                    "final": round(safe_score, 3),
                },
            )
        )
        form.caller_safe = Tristate(safe_value)
    else:
        predictions.append(
            Prediction(label="caller_safe", value=Tristate.UNKNOWN.value, score=0.0,
                       breakdown={"reason": "no engine answered the safety questions"}))

    # --------------------------------------------------------------- hazards
    hazards: list[Hazard] = []
    hazard_probs: dict[str, float] = {}
    hazard_conflict = False
    for hazard in Hazard:
        if hazard is Hazard.NONE:
            continue
        key = f"hazard_{hazard.value}"
        primary = laya_answers.get(key)
        if primary is None:
            continue
        p_true = float(primary.value)
        hazard_probs[hazard.value] = round(p_true, 4)
        cross = laya_cross.get(key)
        tri = _tristate(p_true)
        cross_tri = _tristate(float(cross.value)) if cross else None
        if cross_tri is not None and cross_tri is not tri:
            hazard_conflict = True
            continue  # disagreement means it does not go on the form
        if tri is Tristate.YES:
            if _evidence_for(key, None, original, english) is None:
                # A probability alone cannot invent a hazard that has no
                # locatable context in the caller's words.
                continue
            hazards.append(hazard)

    hazard_score = (
        round(sum(hazard_probs[h.value] for h in hazards) / len(hazards), 3) if hazards else 0.0
    )
    predictions.append(
        Prediction(
            label="hazards",
            value=[h.value for h in hazards],
            score=hazard_score,
            evidence=_evidence_for(
                f"hazard_{hazards[0].value}" if hazards else "hazards", None, original, english
            ),
            conflict=hazard_conflict,
            probabilities=hazard_probs,
            breakdown={
                "per_hazard": hazard_probs,
                "threshold": settings.noul_high,
                "cross_check": "disagree" if hazard_conflict else "agree",
            },
        )
    )
    form.hazards = hazards

    # ------------------------------------------------------- deterministic
    for label in RULE_FIELDS:
        rule = rule_answers.get(label)
        if rule is None:
            predictions.append(
                Prediction(
                    label=label,
                    value=None,
                    score=0.0,
                    breakdown={"reason": "no pattern matched", "extractor": "regex"},
                )
            )
            continue
        score = RULE_FIELD_SCORE.get(label, 0.8) * asr_factor
        predictions.append(
            Prediction(
                label=label,
                value=rule.value,
                score=round(min(1.0, score), 3),
                evidence=_evidence_for(label, rule.quote, original, english),
                decided_on="rules",
                breakdown={
                    "extractor": "regex",
                    "asr_factor": round(asr_factor, 3),
                    "asr_confidence": asr_source,
                    "final": round(min(1.0, score), 3),
                },
            )
        )
        setattr(form, label, rule.value)

    _keep_section_fields(predictions, form, incident_context)

    if laya is None:
        notes.append(
            "Laya was unavailable; categorical fields are filled only where a "
            "quoted phrase matched."
        )
    notes.append(settings.CALIBRATION_WARNING)
    return Resolution(predictions=predictions, form=form, notes=notes)


def _keep_section_fields(predictions: list[Prediction], form: IncidentForm, incident_context: str | None = None) -> None:
    """A medical call does not record how big a fire is, and a fire does not record awake or breathing."""
    drop: list[tuple[str, type]] = []
    incident = IncidentType(incident_context) if incident_context in {v.value for v in IncidentType} else form.incident_type
    drop.extend((name, _ENUMS[name]) for name, spec in DECISIONS.items()
                if incident != spec[0])
    if incident not in FIRE_NATURES:
        drop.extend([("occupancy", Occupancy), ("fire_extent", FireExtent)])
    if incident is not IncidentType.MEDICAL:
        drop.extend([
            ("chief_complaint", ChiefComplaint),
            ("awake", Tristate),
            ("breathing", Tristate),
        ])
    for label, enum in drop:
        current = getattr(form, label)
        if current == enum.UNKNOWN:
            continue
        setattr(form, label, enum.UNKNOWN)
        for prediction in predictions:
            if prediction.label != label:
                continue
            prediction.value = enum.UNKNOWN.value
            prediction.score = 0.0
            prediction.conflict = False
            prediction.alternative = None
            prediction.breakdown = {
                **(prediction.breakdown or {}),
                "out_of_scope": "this detail belongs to a different kind of call",
            }
            break
