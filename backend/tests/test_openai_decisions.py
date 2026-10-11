import pytest
from app.config import settings
from app.decision.openai_decisions import OpenAIDecisionsEngine, questions, parse_answers


def response():
    return {"model": "gpt-6-luna", "answers": [
        {"name": q["name"], "type": "choice", "choice": "unknown", "confidence": 0.8,
         "probabilities": [{"value": c["value"], "probability": 1.0 if c["value"] == "unknown" else 0.0} for c in q["choices"]]}
        for q in questions()
    ]}


def test_unknown_is_not_a_negative():
    answers, notes = parse_answers(response())
    assert answers["weapons_present"].value == 0.5
    assert answers["incident_type"].value == "unknown"
    assert not notes


def test_refusal_supplies_no_model_value():
    data = response()
    name = data["answers"][0]["name"]
    data["answers"][0] = {"type": "refusal", "name": name}
    answers, notes = parse_answers(data)
    assert name not in answers
    assert "declined" in notes[0]


@pytest.mark.parametrize("change", [
    lambda d: d["answers"].pop(),
    lambda d: d["answers"].append(d["answers"][0]),
    lambda d: d["answers"][0].update(choice="unoffered"),
    lambda d: d["answers"][0]["probabilities"][0].update(probability=float("nan")),
])
def test_malformed_responses_fail_instead_of_guessing(change):
    data = response(); change(data)
    with pytest.raises(ValueError): parse_answers(data)


def test_cloud_disabled_never_sends(monkeypatch):
    monkeypatch.setattr(settings, "allow_cloud_inference", False)
    with pytest.raises(RuntimeError, match="cloud inference"):
        OpenAIDecisionsEngine().extract("synthetic input", "", "en")


def test_actual_endpoint_contract_and_sanitized_auth_failure(monkeypatch):
    monkeypatch.setattr(settings, "allow_cloud_inference", True)
    monkeypatch.setattr(settings, "openai_api_key", "test-secret")
    class Success:
        status_code = 200
        is_success = True
        def json(self): return response()
    def post(url, **kwargs):
        assert url == "https://api.openai.com/v1/decisions"
        assert kwargs["json"]["model"] == "gpt-6-luna"
        assert kwargs["json"]["input"] == "Synthetic input"
        return Success()
    monkeypatch.setattr("app.decision.openai_decisions.httpx.post", post)
    assert OpenAIDecisionsEngine().extract("Synthetic input", "", "en").engine == "openai_decisions"
    Success.status_code = 401
    Success.is_success = False
    with pytest.raises(RuntimeError, match="401") as error:
        OpenAIDecisionsEngine().extract("Synthetic input", "", "en")
    assert "test-secret" not in str(error.value)
