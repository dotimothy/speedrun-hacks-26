# Decisions diagnostic evaluation — October 11, 2026

12 synthetic cases, one request per case, current production question set,
gpt-6-luna. No operator sessions were accessed or changed. This is an exploratory
sample, not an accuracy benchmark or a comparison with Laya/Jev.

The fixture expectations were written before the run. Raw answers, probabilities,
resolved predictions, proposals, prompt hash and code revision are preserved in
[the result artifact](decisions-2026-10-11.json). Reproduce with:

```sh
env -u OPENAI_API_KEY PYTHONPATH=backend DISPATCHIQ_ALLOW_CLOUD=1 python scripts/eval_decisions.py --live --output /tmp/decisions-eval.json
```

## Results

All 12 requests succeeded. Median adapter latency was 289.45 ms; maximum 383.8 ms.
20 of 24 predeclared field expectations matched exactly. Three mismatches were
conservative `unknown` answers to whether an explicitly evacuated caller was
trapped: the fixture expected `no`, but the current instructions require explicit
denials. Those are rubric ambiguities, not demonstrated unsafe answers. The
remaining mismatch was a substantive failure to retain expressed uncertainty.
Do not present 20/24 as generalized model accuracy. One run does not establish
stability, calibration, or tail latency.

| Case | Raw Decisions answer | Pipeline result | Diagnosis |
|---|---|---|---|
| Nobody hurt, no weapons | injuries none; weapon no | weapon yes | Keyword override reverses explicit denial |
| Gun corrected to phone, no weapon | weapon no | weapon yes | Historical keyword overrides retraction |
| Cannot distinguish gas from smoke | both unknown | no update or unresolved item | Uncertainty lost downstream |
| Maybe a gas leak, really do not know | gas yes | gas hazard proposal | Model overstates uncertainty; pipeline passes it through |
| Outside, other occupants unaccounted for (two phrasings) | caller evacuated yes | caller safe only | Schema does not represent other occupants' status |
| Burned hand / broken painful wrist | injury confirmed | injury confirmed | Both phrasings worked |
| Nobody hurt, then explicit injury correction | injury confirmed | injury confirmed | Raw classification respected later correction |
| Vague request for help | checked fields unknown | no proposals | Appropriate abstention |

A further representation problem: the Decisions adapter maps yes/no/unknown to
1/0/0.5 for the legacy interface. The hazard composer then treats those codes as
probabilities, producing a score of 1.0 for the uncertain gas example. This is a
pipeline confidence bug, not evidence of model certainty.

## Architectural implications

1. Separate assertion state (asserted, denied, uncertain, unmentioned) from
   probability. Never use numeric encodings of answer labels as confidence.
2. Extract evidence-backed statements with subject identity. Caller safety does
   not describe other occupants; keyword occurrence does not establish truth.
3. Reconcile corrections and retractions before constructing field proposals.
   Historical mentions cannot independently overrule current statements.
4. Preserve explicit uncertainty as reviewable information even when it cannot
   populate a field. Do not create uncertainty alerts merely because a topic
   was never mentioned.
5. Evaluate raw model answers and final workflow separately. Extend this suite
   to sequential acceptance, paraphrases, translations and resolution of
   uncertainty before claiming a general fix. No new keywords were added to
   production code during this evaluation.

Keep Decisions as a candidate for bounded classification. These observations
support fixing the statement representation and policy layer before switching
providers. Laya and Jev were not measured in this run.
