# START_HERE_FOR_AI

This repository is the canonical Grocery Bot Lab workspace for disciplined live/sim experimentation.

Read these files first, in order:
1. `START_HERE_FOR_AI.md`
2. `LIVE_BUDGET_POLICY.md`
3. `AI_EXPERIMENT_WORKFLOW.md`
4. `BRANCH_SCORECARD.md`
5. `EXPERIMENT_JOURNAL.md`

## Quick Commands

### Simulation-first check
```bash
python -m scripts.run_simulation --mode single --seed 0
```

### Live smoke check for canonical baseline (exactly 1 run)
```bash
python -m scripts.run_nmiai_grocery_bot \
  --difficulty expert \
  --legacy-expert-decision-engine \
  --params-file configs/expert_coordination_presets/bundle_a_starvation_relief_empty_only.json \
  --runs 1 \
  --cooldown-sec 1 \
  --record \
  --record-order-trace \
  --record-decision-trace \
  --artifact-root .seed_artifacts/experiments/_smoke
```

### Telemetry-only guard smoke (diagnostics only, actuator off)
```bash
python -m scripts.run_nmiai_grocery_bot \
  --difficulty expert \
  --legacy-expert-decision-engine \
  --params-file configs/expert_coordination_presets/bundle_a_conversion_telemetry_only.json \
  --runs 1 \
  --cooldown-sec 1 \
  --record \
  --record-order-trace \
  --record-decision-trace \
  --artifact-root .seed_artifacts/experiments/_guard_telemetry_smoke
```

### Live batch (max 3 runs)
```bash
python -m scripts.run_nmiai_grocery_bot \
  --difficulty expert \
  --legacy-expert-decision-engine \
  --params-file configs/expert_coordination_presets/bundle_a_starvation_relief_empty_only.json \
  --runs 3 \
  --cooldown-sec 1 \
  --record \
  --record-order-trace \
  --record-decision-trace \
  --artifact-root .seed_artifacts/experiments/_batch
```

### Conversion acceptance gates check
```bash
python -m scripts.check_conversion_acceptance \
  --artifact-root .seed_artifacts/experiments/_batch \
  --difficulty expert \
  --limit 3
```

## Expected Change Style

- Keep canonical `88/9/43` baseline untouched as safety reference.
- Treat conversion guard telemetry as acceptance diagnostics; do not promote emergency actuator.
- Make one narrow, reversible branch hypothesis at a time.
- Validate with medians + conversion acceptance gates, not single-run spikes.
