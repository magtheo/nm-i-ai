# AI_EXPERIMENT_WORKFLOW

Operating manual for AI-driven Grocery Bot experiments.

## 1) Mission and Current Policy

- Primary KPI: live score on expert map.
- Balance score with completed orders, delivered items, throughput, and stability.
- Canonical safety baseline remains `bundle_a_starvation_relief_empty_only` around `88 / 9 / 43`.
- Conversion guard telemetry is promoted as diagnostics.
- Conversion guard emergency actuator is frozen as non-promoted R&D.

## 2) Start-of-Iteration Checklist

1. Read:
   - `BRANCH_SCORECARD.md`
   - latest section of `EXPERIMENT_JOURNAL.md`
   - `LIVE_BUDGET_POLICY.md`
2. Confirm baseline artifact root and reference runs.
3. Confirm whether the task is:
   - baseline safety check,
   - guardrail diagnostics check,
   - new score-seeking branch experiment.
4. Do not patch before artifact inspection.

## 3) Baseline and Artifact Inspection

Minimum:
1. run `result.json`
2. run `config.json`
3. run `decision_trace.jsonl`
4. run `order_trace.json`

Useful commands:

```bash
python -m scripts.compare_runs --artifact-root <root> --difficulty expert --limit 3
python -m scripts.review_run --artifact-dir <run_dir>
python -m scripts.check_conversion_acceptance --artifact-root <root> --difficulty expert --limit 3
```

## 4) Mandatory Conversion Acceptance Gates

Every new branch batch must be checked against conversion continuity.

Required gates:
1. target-liveness
2. drop conversion floor
3. pickup-to-drop coupling
4. commitment realism
5. throughput lane floor
6. delivery-lane guarantee

Implementation:
- Use `scripts/check_conversion_acceptance.py`.
- Treat gate failures as branch health failures even if a single run score spikes.
- Gate checks are acceptance diagnostics, not optimization targets by themselves.

## 5) Guard Branch Policy

- Keep conversion guard telemetry fields in traces and artifacts.
- Keep emergency actuator logic as R&D-only (not promoted preset path).
- Do not spend main iteration budget on actuator tuning unless explicitly requested.
- If actuator is tested, isolate it in a non-promoted branch and report safety impact first.

## 6) Hypothesis Selection Discipline

For each iteration, define:
- bottleneck from traces,
- intended mechanism,
- smallest patch,
- acceptance metrics,
- regression guardrails.

Reject:
- broad multi-mechanism rewrites,
- traffic-polish-only loops,
- score fishing without mechanism evidence.

## 7) Implement Smallest Useful Patch

- Touch minimum files.
- Keep patch reversible.
- Preserve telemetry and artifact compatibility.
- Keep baseline path intact.
- Avoid mixing architecture exploration and tuning sweep in one patch.

## 8) Validation Protocol

1. tests first
2. offline/sim first
3. live smoke: exactly 1 run when needed
4. live batch: at most 3 runs
5. compare medians and conversion gates

Standard live command:

```bash
python -m scripts.run_nmiai_grocery_bot \
  --difficulty expert \
  --legacy-expert-decision-engine \
  --params-file <preset.json> \
  --runs <1_or_3> \
  --cooldown-sec 1 \
  --record \
  --record-order-trace \
  --record-decision-trace \
  --artifact-root <artifact_root>
```

## 9) Compare and Decide

Always report:
- score median
- completed orders median
- delivered items median
- completion rounds (1st/2nd/3rd and 9th/10th if present)
- late `wait_due_to_no_assignment`
- `wait_due_to_collision_block`
- conversion gate pass/fail summary

A branch is promising only when it improves score/completion regime without breaking conversion gates.

## 10) Ceiling and Branch Decision

Use branch-level verdicts:
- `still_scaling`
- `unclear`
- `likely_capped`
- `fork_recommended`

If capped:
- freeze branch as safety/reference,
- open one new score-seeking family branch,
- keep conversion acceptance gates mandatory for that fork.

## 11) Required Iteration Record

Log every serious iteration in `EXPERIMENT_JOURNAL.md`:
1. hypothesis
2. intended mechanism
3. smallest patch
4. files changed
5. validation performed
6. live log findings
7. regression risks
8. verdict
9. next step

## 12) Current Transition Rule

Current operating transition:
- baseline branch is the safety anchor,
- guard telemetry is retained,
- guard actuator promotion is frozen,
- next work should target score-regime change under conversion acceptance discipline.
