"""Synthetic scenario diagnostic through model, confidence and follow-up planning.

Run from the repo root: python scripts/eval_scenario_decisions.py --output results.json
Local Laya by default. Hosted Decisions requires --engine openai_decisions --live.
This small hand-authored suite is a diagnostic, not a calibration benchmark.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.confidence import resolve
from app.decision.questions import ALL_QUESTIONS
from app.decision.rules import RuleEngine
from app.live.diffing import build_suggestions
from app.live.models import Segment, SessionSnapshot, Speaker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["laya", "openai_decisions"], default="laya")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.engine == "openai_decisions":
        if not args.live:
            parser.error("--live is required for a hosted evaluation")
        from app.decision.openai_decisions import OpenAIDecisionsEngine
        engine = OpenAIDecisionsEngine()
    else:
        from app.decision.laya_model import LayaEngine
        engine = LayaEngine()
        if not engine.ready():
            raise SystemExit("Local Laya is unavailable")
    rows = []
    for case in json.loads((ROOT / "backend/evals/scenario_decision_cases.json").read_text()):
        text = case["text"]
        result = engine.extract(text, text, "en")
        resolved = resolve(result, RuleEngine().extract(text, text, "en"), text, text)
        ids = itertools.count()
        outcome = build_suggestions(accepted={}, pending=[], predictions=resolved.predictions,
            segment=Segment(id="caller", index=0, speaker=Speaker.CALLER, original_text=text),
            base_revision=0, next_id=lambda prefix: f"{prefix}-{next(ids)}")
        state = SessionSnapshot(session_id=case["id"], suggestions=outcome.suggestions)
        raw = {name: answer.value for name, answer in result.answers.items() if name in case["expected"]}
        final = {name: resolved.form.model_dump(mode="json")[name] for name in case["expected"]}
        checks = {name: raw.get(name) == expected for name, expected in case["expected"].items()}
        branch_ok = (case["followup"] in {q["field"] for q in state.follow_up_questions}) if "followup" in case else None
        rows.append({**case, "raw":raw, "resolved":final, "checks":checks, "followup_passed":branch_ok,
                     "latency_ms":result.latency_ms, "questions":state.follow_up_questions})
        print(case["id"], "raw", sum(checks.values()), "/", len(checks), "followup", branch_ok, flush=True)
    summary = {"cases":len(rows), "raw_passed":sum(sum(r["checks"].values()) for r in rows),
               "raw_checked":sum(len(r["checks"]) for r in rows),
               "branches_passed":sum(r["followup_passed"] is True for r in rows),
               "branches_checked":sum(r["followup_passed"] is not None for r in rows)}
    report = {"engine":engine.describe(), "summary":summary,
              "question_sha256":hashlib.sha256(json.dumps(ALL_QUESTIONS,sort_keys=True).encode()).hexdigest(), "cases":rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
