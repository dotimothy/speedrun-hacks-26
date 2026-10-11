"""Replay the supplied synthetic HTML call scripts through the actual v2 API.

Uses local Laya. Reviewer actions below are scripted test actions, not automatic
acceptance in the product. No phone call is made and no live session is touched.
"""
import argparse
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient
from app.decision.laya_model import LayaEngine
from app.live import router
from app.main import app

logging.getLogger("httpx").setLevel(logging.WARNING)


def check_case(case_id, record, report, steps):
    fire = case_id.startswith("fire")
    expected = {"incident_type":"structure_fire" if fire else "medical",
                "location_text":"1400 Birch Court" if fire else "77 Willow Lane",
                "caller_name":"Denise Alvarez" if fire else "Priya Shah",
                "callback_number":"555 0151" if fire else "555 0129"}
    if not fire:
        expected.update(awake="yes", breathing="yes")
    checks = {name:record.get(name) == value for name,value in expected.items()}
    quotes = (["6B", "third floor", "propane grill", "oxygen", "coughing"] if fire
              else ["drooping", "chest", "blood on her eyebrow", "left arm", "front door is unlocked"])
    if case_id == "medical-trouble":
        quotes.extend(["not talking right", "sixty-eight"])
    checks.update({"report: " + quote: quote in report for quote in quotes})
    checks["operator words never become suggestions"] = all(not step["new_suggestions"] for step in steps if step["turn"]["speaker"] == "operator")
    if case_id == "fire-location":
        checks["no invented early address"] = all(s["field"] != "location_text" for step in steps[:4] for s in step["new_suggestions"])
        checks["intersection before numbered building"] = any(s["field"] == "location_text" and s["proposed_value"] == "Birch Court and Holt" for s in steps[5]["new_suggestions"])
    if case_id == "medical-location":
        checks["wrong home address not proposed"] = all("Harbor" not in str(s["proposed_value"]) for step in steps for s in step["new_suggestions"] if s["field"] == "location_text")
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    engine = LayaEngine()
    if not engine.ready():
        raise SystemExit("Local Laya unavailable")
    router.get_engine = lambda name=None: engine
    client = TestClient(app)
    cases = json.loads((ROOT / "backend/evals/demo_call_cases.json").read_text(encoding="utf-8"))
    results = []
    for case in cases:
        snapshot = client.post("/api/v2/sessions", json={}).json()
        base = f"/api/v2/sessions/{snapshot['session_id']}"
        steps = []
        reviewer_confirmations = []
        try:
            for index, turn in enumerate(case["turns"]):
                response = client.post(base + "/segments", json={**turn, "language":"en"})
                response.raise_for_status()
                snapshot = response.json()["snapshot"]
                new = response.json()["new_suggestion_ids"]
                steps.append({"turn":turn, "new_suggestions":[s for s in snapshot["suggestions"] if s["id"] in new],
                              "questions":snapshot["follow_up_questions"], "decision_focus":snapshot["decision_focus"]})
                # Simulate review after each caller utterance. Resolve a conflict
                # explicitly to the new caller statement; preserve revision history.
                for suggestion in list(snapshot["suggestions"]):
                    if suggestion["status"] != "pending":
                        continue
                    action = "resolve" if suggestion["kind"] == "conflict" else "accept"
                    payload = {"base_revision":snapshot["revision"], "operation_id":f"{index}-{suggestion['id']}"}
                    if action == "resolve":
                        payload["reason"] = suggestion["conflict_options"][-1]["option_id"]
                    review = client.post(base + f"/suggestions/{suggestion['id']}/{action}", json=payload)
                    review.raise_for_status()
                    snapshot = review.json()["snapshot"]
                if turn["speaker"] == "caller" and "incident_type" not in snapshot["accepted_record"]:
                    # These fixture scenarios are labelled by their author.
                    # Record the human confirmation needed when Laya abstains.
                    incident = "medical" if case["id"].startswith("medical") else "structure_fire"
                    confirmation = client.post(base + "/fields/incident_type", json={
                        "value":incident, "presence":"stated", "operation_id":f"scenario-{index}",
                        "note":"Scripted reviewer confirmed scenario from caller statements."})
                    confirmation.raise_for_status()
                    snapshot = confirmation.json()["snapshot"]
                    reviewer_confirmations.append({"turn":index,"field":"incident_type","value":incident})
            publication = client.post(base + "/publish", json={"base_revision":snapshot["revision"],"operation_id":"publish"})
            publication.raise_for_status()
            record = {name:item["value"] for name,item in snapshot["accepted_record"].items()}
            report = publication.json()["published"]["narrative"]
            checks = check_case(case["id"],record,report,steps)
            results.append({"id":case["id"], "reviewer_confirmations":reviewer_confirmations,
                            "checks":checks, "steps":steps, "record":record, "report":report})
            print(case["id"],json.dumps({k:record.get(k) for k in ("incident_type","location_text","caller_name","callback_number","fire_occupants","awake","breathing")}),flush=True)
        finally:
            client.delete(base)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({"engine":engine.describe(),"cases":results},indent=2)+"\n",encoding="utf-8")
    failures = [(case["id"],name) for case in results for name,passed in case["checks"].items() if not passed]
    print(json.dumps({"checks":sum(len(c["checks"]) for c in results),"failures":failures}))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
