# BRANCH_SCORECARD

Registry for branch-level performance and headroom decisions.

## Status Legend

- `active`: current main experiment path
- `paused`: temporarily inactive
- `capped`: likely near local ceiling, low expected upside
- `deprecated`: no longer a recommended direction

## Branch Scorecard

| Branch Name | Status | Goal | Baseline Score | Best Score | Median Score | Worst Score | Completed Orders Median | Main Bottleneck | Expected Headroom | Last Update |
|---|---|---|---:|---:|---:|---:|---:|---|---|---|
| canonical_active_branch (Bundle A + patched-empty starvation-relief path) | active | Safety baseline for A/B and promotion decisions | 88 | 96* | 88 | 34* | 9 | Near local ceiling; local polish improves smoothness more often than completion cadence | low-to-moderate | 2026-03-10 |
| conversion_guard_rnd_branch (instrumentation + emergency actuator, v1/v2/v3) | paused | Conversion-invariant telemetry R&D and collapse diagnostics | 88 | 88* | 29 | 23 | 3 | Emergency actuator remains too disruptive for promoted use; telemetry is useful | low (as promoted policy), moderate (as diagnostics layer) | 2026-03-10 |
| conversion_safe_targeted_retrieval_fork_v1 (first implemented score-seeking variant) | paused | Conversion-guarded targeted retrieval family aimed at 10th-completion headroom | 88 | 36* | 32 | 31 | 3 | Conversion coupling improved vs initial collapse, but target-liveness still fails hard (`wait_due_to_no_target` ratio ~0.35), keeping score far below baseline | low-to-unclear | 2026-03-11 |
| points_mode_fork_v1 (late-game delivered-only points mode) | deprecated | Attempt completion-cadence regime shift for stable 10th completion | 88 | 89 | 89 | 89 | 9 | Extra item throughput improved, but completion cadence regressed (9th completion moved later; no 10th unlock) | low | 2026-03-10 |
| cadence_controller_fork_v1 (order-age/deficit close mode) | deprecated | State-driven per-order close regime to accelerate cycle cadence | 88 | 88 | 88 | 88 | 9 | Close mode cut preload/fallback work but delayed 9th completion (282 -> 296) with no 10th unlock | low | 2026-03-10 |
| pipeline_budget_fork_v1 (order-budgeted build/secure/transition caps) | deprecated | Add explicit per-order capacity budgets for close/convert/preload/fallback | 88 | 70 | 70 | 70 | 7 | Hard budget gates suppressed productive throughput (pre-pick collapse, higher no-assignment/collision), no 9th completion | low | 2026-03-10 |
| soft_pipeline_budget_fork_v1 (forecast-informed soft budgeting) | deprecated | Apply soft allocation pressure across active close / delivery conversion / preload / fallback | 88 | 78 | 78 | 78 | 8 | Soft penalties still reduced productive work in late cycle; collisions improved but no-assignment surged and 9th completion disappeared | low | 2026-03-10 |
| task_pool_fork_v1 (completion-critical task pool admission) | deprecated | Change assignment generation by allocating a completion-critical bot pool before normal throughput tasks | 88 | 54 | 54 | 54 | 5 | Over-admitted active-close tasks and starved preload/fallback conversion work; no-assignment exploded despite cleaner collisions | low | 2026-03-10 |
| spatial_logistics_fork_v1 (expert_supply role+cluster logistics) | deprecated | New branch family with known-shelf targeted retrieval and spatial role separation | 88 | 2 | 0 | 0 | 0 | Spatial partitioning executed but pick-to-drop conversion collapsed (near-zero drop-offs, no completions) | low | 2026-03-10 |

\* Notes:
- Best/worst include exploratory candidate batches and may include unstable outliers.
- Median is the primary decision KPI for branch progression.
- Conversion guard branch notes:
  - telemetry/invariants are promoted as acceptance diagnostics;
  - emergency actuator path is frozen for non-promoted R&D only.

## Update Rules

When updating this file:
1. Use medians from disciplined batches (not single runs).
2. Keep branch status explicit (`active`, `paused`, `capped`, `deprecated`).
3. If branch is marked `capped`, include reason in `Main Bottleneck`.
4. If branch is resumed, update `Last Update` and expected headroom.
