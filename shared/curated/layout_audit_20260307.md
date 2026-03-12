# Layout Audit 2026-03-07

## Canonical bot root
- `bot — копия`

## Findings
- Shared/global roots are lab-level: `experiments/`, `shared/`, `agents/`, `archive/`.
- No bot-internal `workspace/`, `experiments/`, `agents/`, or `archive/` directories remain.
- Bot-local roots exist: `best/`, `runs/`, `configs/`, `scripts/`.
- Path resolution from inside the bot root resolves to the lab root through `.lab_root`.

## Validation artifacts
- hypothesis created under `experiments/hypotheses/20260307/`
- shared packet written to `shared/inbox/layout_audit_packet.yaml`
- migration manifest: `archive/migration_manifests/correct_repo_layout_20260307.json`

## Remaining risks
- If a second bot folder is introduced later, `canonical_bot.yaml` must be updated explicitly.
- Some older documents outside the active bot tree may still describe the previous layout.
