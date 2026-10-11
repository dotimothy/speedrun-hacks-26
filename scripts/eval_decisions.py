"""Small synthetic diagnostic suite; invokes a paid API only with --live.

PYTHONPATH=backend python scripts/eval_decisions.py --live --output /tmp/results.json
No running server or operator sessions are accessed. Expectations are authored
before execution and remain inspectable; this is not a calibrated benchmark.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import statistics
import subprocess

from app.confidence import resolve
from app.decision.openai_decisions import OpenAIDecisionsEngine, questions
from app.decision.rules import RuleEngine
from app.live.diffing import build_suggestions
from app.live.models import Segment, Speaker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required to authorize the hosted evaluation')
    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / 'backend/evals/decision_cases.json').read_text())
    engine, rules = OpenAIDecisionsEngine(), RuleEngine()
    rows = []
    for case in cases:
        text = case['text']
        try:
            result = engine.extract(text, text, 'en')
            resolution = resolve(result, rules.extract(text, text, 'en'), text, text)
            ids = itertools.count(1)
            outcome = build_suggestions(accepted={}, pending=[], predictions=resolution.predictions,
                segment=Segment(id='seg-1', index=0, speaker=Speaker.CALLER, original_text=text),
                base_revision=0, next_id=lambda prefix: f'{prefix}-{next(ids)}')
            checks = [{ 'field': k, 'expected': v,
                        'actual': result.answers[k].value if k in result.answers else None,
                        'passed': k in result.answers and result.answers[k].value == v}
                      for k,v in case['expected'].items()]
            rows.append({**case, 'engine': result.engine, 'model': result.model,
                'latency_ms': result.latency_ms, 'checks': checks,
                'raw_answers': {k: asdict(v) for k,v in result.answers.items()},
                'predictions': [p.model_dump(mode='json') for p in resolution.predictions],
                'suggestions': [s.model_dump(mode='json') for s in outcome.suggestions],
                'unresolved': [u.model_dump(mode='json') for u in outcome.unresolved],
                'flags': [f.model_dump(mode='json') for f in outcome.flags]})
            print(case['id'], sum(c['passed'] for c in checks), '/',len(checks), flush=True)
        except Exception as error:
            # Do not serialize arbitrary provider exceptions or credential fragments.
            rows.append({**case, 'error_type': type(error).__name__})
            print(case['id'],type(error).__name__, flush=True)
    checks = [c for row in rows for c in row.get('checks', [])]
    latencies = [r['latency_ms'] for r in rows if 'latency_ms' in r]
    summary = {'cases':len(rows),'errors':sum('error_type' in r for r in rows),
               'passed':sum(c['passed'] for c in checks),'checked':len(checks),
               'median_ms':statistics.median(latencies) if latencies else None,
               'max_ms':max(latencies) if latencies else None}
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
              'git_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
              'question_sha256':hashlib.sha256(json.dumps(questions(),sort_keys=True).encode()).hexdigest(),
              'summary':summary,'cases':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
