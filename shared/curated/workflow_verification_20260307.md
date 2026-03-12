# Workflow Verification 2026-03-07

The stabilized lab/bot layout was exercised end-to-end for the first real experiment lifecycle.

Completed:
- promoted the canonical expert baseline into `bot — копия/best/expert/current.json`
- recorded metadata in `bot — копия/best/expert/metadata.yaml`
- registered the promoted baseline in `shared/shared_state/active_baselines.yaml`
- created a real expert hypothesis package under `experiments/hypotheses/20260307/`
- wrote an experiment handoff packet to `shared/inbox/`

Result:
- bot-local assets remained inside the bot root
- experiment and collaboration artifacts landed at lab root as intended
