# Expert Baseline Provenance Recovery — 2026-03-07

## Target
Recover the actual run-backed config for the historical stable expert 82 profile.

## Result
Exact recovery was not possible from the current workspace.

## Strongest available provenance
The active expert baseline is the best available local fallback:
- source config: `configs/expert_baseline_current.json`
- source type: parsed defaults from `decision_engine.py`
- promotion id: `expert_baseline_20260307`
- current status: `bootstrap_only`

## Evidence checked
- `best/expert/current.json`
- `best/expert/metadata.yaml`
- `configs/expert_baseline_current.json`
- `.seed_artifacts/nmiai/` contents
- `artifacts/` contents
- `bot_context.md`
- `MAXSCORE_ENGINE_NOTES.md`

## Gap
No preserved expert run-backed config, `app/integrations/nmiai_grocery_bot/best_configs/expert.json`, or expert run artifact with score 82 was found in the active workspace.

## Implication
The current expert baseline remains a bootstrap comparison reference until a real expert live-best config or scored expert run artifact is recovered.
