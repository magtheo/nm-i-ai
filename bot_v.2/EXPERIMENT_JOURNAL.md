# EXPERIMENT_JOURNAL

Use this file as the durable experiment log for future AI sessions.

## Entry Template

### Date / Iteration ID
- Date:
- Iteration ID:

### Branch
- Branch name:
- Status:

### Hypothesis
- Bottleneck observed:
- Why this bottleneck matters now:

### Intended Mechanism
- Expected behavior change:
- Why this should improve KPI:

### Smallest Patch
- Files touched:
- Scope boundary (what was intentionally not changed):

### Validation Performed
- Simulation/offline checks:
- Live smoke check:
- Live batch size:
- Artifact roots:

### Live Log Findings
- Score / orders / items:
- Completion rounds:
- Tail behavior:
- Collision/queue/no-assignment findings:
- Mechanism-specific telemetry evidence:

### Regression Risks
- Main risks observed:
- What must be monitored next:

### Verdict
- One of: accepted / rejected / candidate / revert_candidate
- Why:

### Next Step
- Single next hypothesis:
- Required evidence:

---

## Current Baseline Snapshot (Reference)

- Date: 2026-03-10
- Branch: canonical active branch in this workspace
- Baseline (validated median):
  - score: 88
  - completed orders: 9
  - delivered items: 43
  - completion rounds: 54 / 91 / 118
- Known current limiter:
  - late completion-cadence / tail-control tradeoff
  - crowding/collision polishing alone does not reliably raise completion regime

---

## 2026-03-10 / points_mode_fork_v1

### Date / Iteration ID
- Date: 2026-03-10
- Iteration ID: points_mode_fork_v1

### Branch
- Branch name: points_mode_fork_v1 (late-game delivered-only points mode)
- Status: rejected

### Hypothesis
- Bottleneck observed: canonical branch reaches 9th completion late (round ~282 median) and fails to unlock stable 10th; late rounds still carry high no-assignment pressure.
- Why this bottleneck matters now: without a completion-cadence shift, branch stays near local ceiling around score 88/9 orders.

### Intended Mechanism
- Expected behavior change: in final rounds, switch primary demand accounting to delivered-only and force matching-cargo delivery to prioritize points-per-round closure.
- Why this should improve KPI: fewer optimistic commitment assumptions in late game should reduce false coverage and convert actions into active completion progress.

### Smallest Patch
- Files touched:
  - `bot/decision_engine.py`
  - `configs/expert_coordination_presets/bundle_a_points_mode_fork_v1.json`
  - `tests/test_decision_engine_points_mode.py`
- Scope boundary (what was intentionally not changed): no planner rewrite, no role redesign, no preview architecture rewrite, no fallback crowding retune.

### Validation Performed
- Simulation/offline checks:
  - `python -m pytest -q` (29 passed)
  - single-run simulator baseline vs candidate using expert-derived dataset snapshot source (both 89/44/9 in that harness)
- Live smoke check:
  - yes, exactly 1 run (`score=89, orders=9, items=44`)
- Live batch size:
  - 3 runs (all 89/44/9)
- Artifact roots:
  - smoke: `.seed_artifacts/experiments/branch_points_mode_fork_v1/smoke`
  - batch: `.seed_artifacts/experiments/branch_points_mode_fork_v1/batch`

### Live Log Findings
- Score / orders / items:
  - median `89 / 9 / 44` vs canonical baseline `88 / 9 / 43`
- Completion rounds:
  - first/second/third unchanged: `54 / 91 / 118`
  - 9th completion regressed from `282` to `293`; no 10th completion
- Tail behavior:
  - `late_game_points_mode_active` engaged for 56 rounds; delivered-only demand active for same rounds
- Collision/queue/no-assignment findings:
  - collision waits improved (`167 -> 118`)
  - no-assignment waits worsened (`873 -> 1068` total; late 180+ `214 -> 409`)
- Mechanism-specific telemetry evidence:
  - delivered-fallback assignments dropped (`269 -> 146`)
  - late pre-pick assignments dropped (`132 -> 82`)
  - mechanism reduced fallback traffic but did not improve completion cadence

### Regression Risks
- Main risks observed:
  - late-mode over-focus on immediate delivery can suppress productive assignment churn needed for 10th-completion timing
  - lower collision is achieved partly by less effective completion progress, not better closure throughput
- What must be monitored next:
  - 9th/10th completion timing, not only score and collision metrics
  - conversion of late-round assignments into order closures

### Verdict
- One of: accepted / rejected / candidate / revert_candidate
- rejected
- Why:
  - no completion-regime shift; 9th completion became later; no stable 10th unlock despite +1 item median.

### Next Step
- Single next hypothesis:
  - forecast-driven cadence fork (multi-order lookahead objective), not another late-traffic polish.
- Required evidence:
  - stable earlier 9th completion and first reproducible 10th completion in a 3-run batch.

---

## 2026-03-10 / cadence_controller_fork_v1

### Date / Iteration ID
- Date: 2026-03-10
- Iteration ID: cadence_controller_fork_v1

### Branch
- Branch name: cadence_controller_fork_v1 (order-age/deficit close controller)
- Status: rejected

### Hypothesis
- Bottleneck observed: baseline has no explicit per-order cadence controller, so late orders can stay in preload/secondary mix even when behind closure target.
- Why this bottleneck matters now: to unlock stable 10th completion, the system needs earlier order-close pressure, not only end-of-game pressure.

### Intended Mechanism
- Expected behavior change: activate a close regime per active order when either:
  - active order age exceeds a target, or
  - delivered-only deficit is in tail range.
- Close regime changes:
  - demand accounting to delivered-only,
  - always deliver matching cargo,
  - disable transition stash while close mode is active.

### Smallest Patch
- Files touched:
  - `bot/decision_engine.py`
  - `configs/expert_coordination_presets/bundle_a_cadence_controller_fork_v1.json`
  - `tests/test_decision_engine_cadence_controller.py`
- Scope boundary (what was intentionally not changed):
  - no planner rewrite
  - no role-system rewrite
  - no fallback soft-penalty retuning
  - no global late-round toggles

### Validation Performed
- Simulation/offline checks:
  - `python -m pytest -q` (33 passed)
  - sim single-seed baseline vs candidate on expert-derived dataset: both `89/44/9`
- Live smoke check:
  - yes, exactly 1 run (`88/43/9`)
- Live batch size:
  - 3 runs (`88/43/9` for all three)
- Artifact roots:
  - smoke: `.seed_artifacts/experiments/branch_cadence_controller_fork_v1/smoke`
  - batch: `.seed_artifacts/experiments/branch_cadence_controller_fork_v1/batch`

### Live Log Findings
- Score / orders / items:
  - unchanged vs baseline median: `88 / 9 / 43`
- Completion rounds:
  - first/second/third unchanged: `54 / 91 / 118`
  - 9th completion regressed: `282 -> 296`
  - 10th completion remained absent
- Tail behavior:
  - close mode activated (median 48 rounds/run), so mechanism executed
- Collision/queue/no-assignment findings:
  - collision waits improved (`167 -> 150`)
  - no-assignment waits slightly worsened (`873 -> 877`)
- Mechanism-specific telemetry evidence:
  - late pre-pick assignments dropped (`132 -> 60`)
  - delivered-fallback assignments dropped (`269 -> 235`)
  - mechanism reduced preload/fallback pressure but did not improve closure cadence

### Regression Risks
- Main risks observed:
  - close mode can suppress useful preload too early, delaying next completion boundary
  - collision reduction can come from reduced productive work rather than faster closure
- What must be monitored next:
  - 9th/10th completion timing as primary mechanism KPI
  - active-to-next-order handoff speed, not only collision/no-assignment totals

### Verdict
- One of: accepted / rejected / candidate / revert_candidate
- rejected
- Why:
  - no completion-regime shift and worse 9th completion timing.

### Next Step
- Single next hypothesis:
  - forecast-informed completion-cadence planner (order-budgeted active/preview allocation using multi-order horizon).
- Required evidence:
  - earlier 9th completion and first stable 10th completion in a 3-run batch without median score regression.

---

## 2026-03-10 / pipeline_budget_fork_v1

### Date / Iteration ID
- Date: 2026-03-10
- Iteration ID: pipeline_budget_fork_v1

### Branch
- Branch name: pipeline_budget_fork_v1 (order-budgeted active/secure/transition pipeline)
- Status: rejected

### Hypothesis
- Bottleneck observed: baseline lacks explicit per-order capacity budgeting across active close, delivery conversion, preview preload, and fallback.
- Why this bottleneck matters now: without explicit budget split, local assignment improvements were not unlocking stable 10th-completion cadence.

### Intended Mechanism
- Expected behavior change: introduce pipeline modes driven by delivered/committed active deficit:
  - `build`: force active close, suppress preview pressure
  - `secure`: cap extra active search and reserve more delivery conversion
  - `transition`: allow bounded preview preload after closure is effectively secured
- Why this should improve KPI: explicit budget control should reduce noisy fallback/preload work and improve order-cycle conversion.

### Smallest Patch
- Files touched:
  - `bot/assignment.py`
  - `bot/decision_engine.py`
  - `configs/expert_coordination_presets/bundle_a_pipeline_budget_fork_v1.json`
  - `tests/test_assignment_pipeline_budget.py`
- Scope boundary (what was intentionally not changed):
  - no engine-family swap
  - no role-system rewrite
  - no dropoff/traffic architecture rewrite
  - no late-game global mode toggles

### Validation Performed
- Simulation/offline checks:
  - `python -m pytest tests/test_assignment_pipeline_budget.py -q` (2 passed)
  - `python -m pytest -q` (35 passed)
  - offline trace comparison artifact:
    - `.seed_artifacts/experiments/branch_pipeline_budget_fork_v1/analysis_baseline_vs_pipeline_budget.json`
- Live smoke check:
  - yes, exactly 1 run (`70/35/7`)
- Live batch size:
  - 3 runs (`70/35/7` for all three)
- Artifact roots:
  - smoke: `.seed_artifacts/experiments/branch_pipeline_budget_fork_v1/smoke`
  - batch: `.seed_artifacts/experiments/branch_pipeline_budget_fork_v1/batch`

### Live Log Findings
- Score / orders / items:
  - candidate median `70 / 7 / 35` vs baseline `88 / 9 / 43`
- Completion rounds:
  - baseline first/second/third: `54 / 91 / 118`
  - candidate first/second/third: `56 / 117 / 150`
  - baseline 9th completion: `282`; candidate had no 9th completion
- Tail behavior:
  - pipeline mode executed (`pipeline_build` + `pipeline_secure` tags present), but `pipeline_transition` never activated in the batch.
- Collision/queue/no-assignment findings:
  - `wait_due_to_no_assignment`: `873 -> 1040` (late 180+: `214 -> 377`)
  - `wait_due_to_collision_block`: `167 -> 204` (late 180+: `72 -> 111`)
- Mechanism-specific telemetry evidence:
  - pre-pick assignments collapsed from `315` to `90` median
  - all candidate pre-picks came from pipeline-tagged sources
  - conversion pressure and preload suppression were too strict and cut productive throughput

### Regression Risks
- Main risks observed:
  - over-restrictive pipeline budgets suppress productive work before closure
  - secure/build gating can trap the system in low-throughput states with elevated no-assignment waits
  - disabling transition stash under pipeline mode removed useful preload behavior
- What must be monitored next:
  - mode occupancy by round (`build/secure/transition`)
  - marginal completion gain per budget gate (not only cleaner control metrics)

### Verdict
- One of: accepted / rejected / candidate / revert_candidate
- rejected
- Why:
  - architecture mechanism executed but decisively regressed completion cadence and score regime.

### Next Step
- Single next hypothesis:
  - implement softer order budgets as ranking influence (not hard caps), preserving preload throughput while adding forecast-informed allocation.
- Required evidence:
  - at least baseline-level median (`88/9/43`) with earlier/equal 9th completion and first reproducible 10th completion signal.

---

## 2026-03-10 / soft_pipeline_budget_fork_v1

### Date / Iteration ID
- Date: 2026-03-10
- Iteration ID: soft_pipeline_budget_fork_v1

### Branch
- Branch name: soft_pipeline_budget_fork_v1 (forecast-informed soft budgeting via ranking pressure)
- Status: rejected

### Hypothesis
- Bottleneck observed: branch lacks soft per-order allocation pressure; hard caps failed by pruning productive work.
- Why this bottleneck matters now: to unlock completion-cadence headroom, control should bias work mix (active close / delivery conversion / preload / fallback) without reducing total assignment throughput.

### Intended Mechanism
- Expected behavior change:
  - infer order-phase mode (`build` / `secure` / `transition`) from delivered-only vs committed active deficit;
  - apply soft utility terms only (no hard caps):
    - active-close bonus on active picks,
    - delivery-conversion priority bonus for matching-cargo carriers,
    - preview preload discount while active tail is open,
    - fallback soft penalty while active tail is open.
- Why this should improve KPI: preserve assignment volume while shifting marginal work toward faster completion conversion.

### Smallest Patch
- Files touched:
  - `bot/assignment.py`
  - `bot/decision_engine.py`
  - `configs/expert_coordination_presets/bundle_a_soft_pipeline_budget_fork_v1.json`
  - `tests/test_assignment_soft_pipeline_budget.py`
- Scope boundary (what was intentionally not changed):
  - no planner-family rewrite
  - no hard caps
  - no hard close toggles
  - no traffic-only polish pass

### Validation Performed
- Simulation/offline checks:
  - `python -m pytest tests/test_assignment_soft_pipeline_budget.py -q` (2 passed)
  - `python -m pytest -q` (37 passed)
  - `python -m scripts.run_simulation ...` failed due missing local medium dataset root (`.seed_artifacts/nmiai/medium`)
  - offline replay probe on canonical expert states:
    - `.seed_artifacts/experiments/branch_soft_pipeline_budget_fork_v1/offline_probe.json`
  - baseline vs candidate trace comparison:
    - `.seed_artifacts/experiments/branch_soft_pipeline_budget_fork_v1/analysis_baseline_vs_soft_pipeline_budget.json`
- Live smoke check:
  - yes, exactly 1 run (`78/38/8`)
- Live batch size:
  - 3 runs (`78/38/8` for all three)
- Artifact roots:
  - smoke: `.seed_artifacts/experiments/branch_soft_pipeline_budget_fork_v1/smoke`
  - batch: `.seed_artifacts/experiments/branch_soft_pipeline_budget_fork_v1/batch`

### Live Log Findings
- Score / orders / items:
  - candidate median `78 / 8 / 38` vs baseline `88 / 9 / 43`
- Completion rounds:
  - first/second/third unchanged: `54 / 91 / 118`
  - baseline 9th completion `282`; candidate had no 9th completion
  - candidate completion switches ended at round `297` with only 8 completed orders
- Tail behavior:
  - soft mode executed (`build` + `secure` tags present), `transition` mode did not activate.
  - run-level mode occupancy (representative run): build `130` rounds, secure `170`, transition `0`.
- Collision/queue/no-assignment findings:
  - `wait_due_to_collision_block`: `167 -> 114` (late `72 -> 19`) improved
  - `wait_due_to_no_assignment`: `873 -> 1189` (late `214 -> 530`) regressed sharply
- Mechanism-specific telemetry evidence:
  - productive assignments dropped `1394 -> 1203`
  - pre-pick assignments dropped `315 -> 203`
  - secondary assignments dropped `269 -> 170`
  - soft-tagged assignments present (`soft_pipeline_source_count` median `2492`), so mechanism was active

### Regression Risks
- Main risks observed:
  - even soft preload/fallback discounts can over-suppress useful late-cycle work
  - collision improvements can still come from reduced productive activity
  - absence of transition mode suggests mode criteria are not aligned with expert order flow
- What must be monitored next:
  - productive assignment volume by phase, not only collision/no-assignment totals
  - 8th->9th order handoff latency and whether preload pressure is too damped in secure mode

### Verdict
- One of: accepted / rejected / candidate / revert_candidate
- rejected
- Why:
  - mechanism executed and reduced collisions, but lowered throughput and failed completion-cadence objective (no 9th completion).

### Next Step
- Single next hypothesis:
  - state-continuous soft controller (smaller deltas, not discrete mode penalties) tied to marginal completion gain, preserving secondary/preload throughput in secure tail.
- Required evidence:
  - baseline-level throughput (`>=43` items median) with at least equal 9th-completion timing and first repeatable 10th-completion signal.

---

## 2026-03-10 / task_pool_fork_v1

### Date / Iteration ID
- Date: 2026-03-10
- Iteration ID: task_pool_fork_v1

### Branch
- Branch name: task_pool_fork_v1 (completion-critical task pool admission)
- Status: rejected

### Hypothesis
- Bottleneck observed: current assignment layer generates the wrong work mix; ranking-only tuning cannot change completion regime.
- Why this bottleneck matters now: branch needs structural control over which tasks are admitted and which bots are allocated to completion-critical work.

### Intended Mechanism
- Expected behavior change:
  - create an explicit completion-critical bot pool each round using active-deficit signals and nearest-to-active proximity;
  - force those bots to attempt active-only tasks first;
  - allow fallback to normal pool if no active option exists (avoid hard-pruning throughput).
- Why this should improve KPI: task admission/allocation structure should raise active-close pressure without relying on penalty/weight tuning.

### Smallest Patch
- Files touched:
  - `bot/assignment.py`
  - `bot/decision_engine.py`
  - `configs/expert_coordination_presets/bundle_a_task_pool_fork_v1.json`
  - `tests/test_assignment_task_pool_admission.py`
- Scope boundary (what was intentionally not changed):
  - no planner-family rewrite
  - no new soft/hard budgeting toggles
  - no traffic/collision-only tuning pass

### Validation Performed
- Simulation/offline checks:
  - `python -m pytest tests/test_assignment_task_pool_admission.py -q` (2 passed)
  - `python -m pytest -q` (39 passed)
  - `python -m scripts.run_simulation ...` unavailable (missing `.seed_artifacts/nmiai/medium` dataset)
  - offline probe on canonical expert states:
    - `.seed_artifacts/experiments/branch_task_pool_fork_v1/offline_probe.json`
  - baseline vs candidate analysis:
    - `.seed_artifacts/experiments/branch_task_pool_fork_v1/analysis_baseline_vs_task_pool_fork.json`
- Live smoke check:
  - yes, exactly 1 run (`54/29/5`)
- Live batch size:
  - 3 runs (`54/29/5` for all three)
- Artifact roots:
  - smoke: `.seed_artifacts/experiments/branch_task_pool_fork_v1/smoke`
  - batch: `.seed_artifacts/experiments/branch_task_pool_fork_v1/batch`

### Live Log Findings
- Score / orders / items:
  - candidate median `54 / 5 / 29` vs baseline `88 / 9 / 43`
- Completion rounds:
  - candidate first/second/third: `61 / 89 / 160`
  - baseline first/second/third: `54 / 91 / 118`
  - baseline 9th completion `282`; candidate had no 9th completion
- Tail behavior:
  - critical-pool mechanism executed (`critical_pool_assignments` median `575`)
  - critical fallback path unused (`critical_pool_fallback_assignments` median `0`)
- Collision/queue/no-assignment findings:
  - collision waits improved (`167 -> 70`; late `72 -> 17`)
  - no-assignment waits regressed severely (`873 -> 1589`; late `214 -> 773`)
- Mechanism-specific telemetry evidence:
  - active picks surged (`287 -> 642`)
  - preview/fallback work collapsed (`pre_pick 315 -> 102`, `secondary 269 -> 0`)
  - productive assignments fell overall (`1394 -> 1140`)
  - net result: lower conversion and completion cadence despite cleaner local traffic

### Regression Risks
- Main risks observed:
  - over-admission of active-close missions can starve preload/fallback channels needed for sustained order pipeline
  - structural separation without balanced conversion control can reduce total productive throughput
  - collision/no-overlap improvements can again be achieved by doing less useful work
- What must be monitored next:
  - per-phase productive assignment volume
  - active-pick to drop-off conversion efficiency
  - whether critical pool can release capacity dynamically when conversion lags

### Verdict
- One of: accepted / rejected / candidate / revert_candidate
- rejected
- Why:
  - architecture changed as intended, but outcome regressed both throughput and completion regime.

### Next Step
- Single next hypothesis:
  - two-lane admission with explicit conversion lane guard (ensure minimum delivery+preload throughput while admitting critical picks), not single-pool active over-admission.
- Required evidence:
  - no throughput collapse (`items >= 43` median) with equal/better 9th-completion timing and first repeatable 10th-completion signal.

---

## 2026-03-10 / spatial_logistics_fork_v1

### Date / Iteration ID
- Date: 2026-03-10
- Iteration ID: spatial_logistics_fork_v1

### Branch
- Branch name: spatial_logistics_fork_v1 (expert_supply role+cluster logistics family)
- Status: rejected

### Hypothesis
- Bottleneck observed: current planner/assignment family appears capped; new regime likely requires different physical logistics, not assignment tuning.
- Why this bottleneck matters now: repeated tuning forks improved local smoothness but failed completion-regime shift.

### Intended Mechanism
- Expected behavior change:
  - switch to `ExpertSupplyStrategyEngine` family (no legacy assignment engine);
  - enforce role-separated spatial behavior:
    - harvester cluster preference (`upper/lower`) with cluster-first target enforcement;
    - courier local active-only harvest radius around drop corridor;
    - flex remains critical dispatch.
  - preserve targeted shelf retrieval from known supply map.
- Why this should improve KPI: physically separate active-close, delivery, and throughput regions to reduce mixed-flow interference and improve conversion.

### Smallest Patch
- Files touched:
  - `bot/expert_supply_strategy.py`
  - `tests/test_expert_supply_strategy.py`
- Scope boundary (what was intentionally not changed):
  - no edits to canonical legacy decision-engine baseline path
  - no additional weighting/budget/close-mode forks in legacy planner
  - no repo-wide rewrite

### Validation Performed
- Simulation/offline checks:
  - `python -m pytest tests/test_expert_supply_strategy.py -q` (17 passed)
  - `python -m pytest -q` (41 passed)
  - offline replay probe:
    - `.seed_artifacts/experiments/branch_spatial_logistics_fork_v1/offline_probe_expert_supply.json`
  - baseline vs candidate comparison:
    - `.seed_artifacts/experiments/branch_spatial_logistics_fork_v1/analysis_baseline_vs_spatial_logistics_fork.json`
  - `run_simulation` not usable for this branch in current workspace (missing `.seed_artifacts/nmiai/medium` dataset)
- Live smoke check:
  - yes, exactly 1 run (`0/0/0`)
- Live batch size:
  - 3 runs (`0/0/0`, `2/0/2`, `0/0/0`)
- Artifact roots:
  - smoke: `.seed_artifacts/experiments/branch_spatial_logistics_fork_v1/smoke`
  - batch: `.seed_artifacts/experiments/branch_spatial_logistics_fork_v1/batch`

### Live Log Findings
- Score / orders / items:
  - candidate median `0 / 0 / 0` vs baseline `88 / 9 / 43`
- Completion rounds:
  - baseline first/second/third `54 / 91 / 118`, 9th `282`
  - candidate had no completions
- Tail behavior:
  - candidate spent most rounds in active/critical phases without transition conversion
- Collision/queue/no-assignment findings:
  - collision waits increased (`167 -> 329` median)
  - `wait_due_to_no_target` became dominant (`1690` median)
  - drop-off conversion collapsed (`drop_off` actions median `0`)
- Mechanism-specific telemetry evidence:
  - mechanism executed (`cluster_match_targets` median `300`, `courier_active_targets` median `461`)
  - but conversion path failed: many picks, almost no successful delivery conversion

### Regression Risks
- Main risks observed:
  - hard spatial separation can starve conversion flow when inventory-to-drop path is not explicitly guaranteed
  - local “correctness” of zone behavior can coexist with near-zero scoring if delivery loop is under-coupled
  - this family needs explicit conversion guarantees, not just spatial partitioning
- What must be monitored next:
  - pick-to-drop conversion ratio as a hard gate metric
  - minimum guaranteed delivery lane occupancy
  - active item carry duration before drop-off

### Verdict
- One of: accepted / rejected / candidate / revert_candidate
- rejected
- Why:
  - branch-family shift is real, but current implementation collapsed score/completions and failed basic conversion.

### Next Step
- Single next hypothesis:
  - if this family is retried, add explicit conversion guardrails (pickup admission coupled to guaranteed delivery slots), or start a separate spatial family with built-in pick-to-drop invariants.
- Required evidence:
  - non-collapsing baseline viability first (`orders >= 5`, `items >= 25`) before attempting regime-shift claims.

---

## 2026-03-10 / conversion_guard_freeze_and_guarded_fork_bootstrap_v1

### Date / Iteration ID
- Date: 2026-03-10
- Iteration ID: conversion_guard_freeze_and_guarded_fork_bootstrap_v1

### Branch
- Branch name: conversion_guard_rnd_branch (freeze) + conversion_safe_targeted_retrieval_fork_v1 (bootstrap)
- Status: candidate

### Hypothesis
- Bottleneck observed:
  - conversion telemetry is valuable, but promoted emergency intervention remains unsafe across batches.
  - score-seeking work needs hard acceptance discipline on conversion continuity.
- Why this bottleneck matters now:
  - without explicit acceptance gates, new high-upside branches can repeat non-scoring collapse despite local metric improvements.

### Intended Mechanism
- Expected behavior change:
  - freeze guard actuator promotion work (R&D-only);
  - preserve and surface conversion invariants as mandatory acceptance checks;
  - bootstrap next score-seeking family under conversion-safe gates from day 1.
- Why this should improve KPI:
  - shifts effort from unsafe actuator tuning to higher-upside score branching, while preventing collapse-class regressions.

### Smallest Patch
- Files touched:
  - `scripts/check_conversion_acceptance.py`
  - `tests/test_check_conversion_acceptance.py`
  - `configs/expert_coordination_presets/bundle_a_conversion_telemetry_only.json`
  - `START_HERE_FOR_AI.md`
  - `AI_EXPERIMENT_WORKFLOW.md`
  - `BRANCH_SCORECARD.md`
  - `EXPERIMENT_JOURNAL.md`
- Scope boundary (what was intentionally not changed):
  - no further guard actuator tuning
  - no planner architecture rewrite in this iteration
  - no promoted baseline behavior change

### Validation Performed
- Simulation/offline checks:
  - `python -m pytest tests/test_check_conversion_acceptance.py -q`
  - `python -m scripts.check_conversion_acceptance --artifact-root .seed_artifacts/experiments/bundle_a_soft_spatial_iter4/candidate_soft_penalty_v3 --difficulty expert --limit 3`
  - `python -m scripts.check_conversion_acceptance --artifact-root .seed_artifacts/experiments/branch_conversion_guard_reason_weighting_v3/batch --difficulty expert --limit 3`
- Live smoke check:
  - no new live run (not required for documentation/instrumentation freeze task)
- Live batch size:
  - 0
- Artifact roots:
  - baseline reference: `.seed_artifacts/experiments/bundle_a_soft_spatial_iter4/candidate_soft_penalty_v3/expert`
  - guard reference: `.seed_artifacts/experiments/branch_conversion_guard_reason_weighting_v3/batch/expert`

### Live Log Findings
- Score / orders / items:
  - baseline reference remains around `88 / 9 / 43`.
  - guard v2/v3 promoted behavior remained unsafe (`29 / 3 / 14` then `23 / 2 / 13` median in tracked batches).
- Completion rounds:
  - baseline reference: `54 / 91 / 118` with 9th completion at `282`.
  - guard branches: cadence collapse and unstable later completions.
- Tail behavior:
  - telemetry shows coupling/commitment/throughput warning signals before poor outcomes.
- Collision/queue/no-assignment findings:
  - guard branches lowered some collision counts but still damaged productive conversion.
- Mechanism-specific telemetry evidence:
  - reason-weighting reduced coupling-trigger dominance, but promotion safety did not recover.

### Regression Risks
- Main risks observed:
  - conversion acceptance thresholds can be set too strict and reject viable branches.
  - diagnostics-only preset could be confused with promoted policy if docs are unclear.
- What must be monitored next:
  - false-positive/false-negative rates of acceptance gates;
  - whether next score-seeking fork keeps throughput while passing conversion gates.

### Verdict
- One of: accepted / rejected / candidate / revert_candidate
- accepted (workflow/guardrail transition)
- Why:
  - branch governance is cleaner: telemetry retained, unsafe actuator promotion frozen, next fork bootstrapped under explicit conversion acceptance discipline.

### Next Step
- Single next hypothesis:
  - conversion-safe targeted retrieval family: known-shelf targeted pickup with explicit delivery-lane guarantee and conversion-floor admission.
- Required evidence:
  - median score/completion improvement over baseline without conversion-gate failures.

---

## 2026-03-10 / conversion_safe_targeted_retrieval_fork_v1_first_impl

### Date / Iteration ID
- Date: 2026-03-10
- Iteration ID: conversion_safe_targeted_retrieval_fork_v1_first_impl

### Branch
- Branch name: conversion_safe_targeted_retrieval_fork_v1
- Status: rejected (first implementation)

### Hypothesis
- Bottleneck observed:
  - canonical branch is near ceiling; needed new family with known-shelf targeted retrieval and stronger physical pick->drop coupling.
- Why this bottleneck matters now:
  - prior tuning/budget/mode forks could not unlock completion-regime shift.

### Intended Mechanism
- Expected behavior change:
  - dual-lane active retrieval on known shelves:
    - strict deficit retrieval;
    - sustain active retrieval when optimistic commitment suppresses targets;
  - stronger delivery conversion lane:
    - couriers with active cargo deliver immediately;
    - delivery-lane force when active conversion stalls;
  - stage fallback for empty bots toward active-needed shelves (avoid no-target starvation).
- Why this should improve KPI:
  - maintain target-liveness and conversion continuity while testing spatially targeted retrieval family.

### Smallest Patch
- Files touched:
  - `bot/expert_supply_strategy.py`
  - `tests/test_expert_supply_strategy.py`
  - `scripts/check_conversion_acceptance.py`
  - `BRANCH_SCORECARD.md`
  - `EXPERIMENT_JOURNAL.md`
- Scope boundary (what was intentionally not changed):
  - no legacy DecisionEngine baseline changes
  - no guard actuator promotion/tuning
  - no repo-wide planner rewrite

### Validation Performed
- Simulation/offline checks:
  - `python -m pytest tests/test_expert_supply_strategy.py -q` (21 passed)
  - `python -m pytest -q` (49 passed)
  - `python -m scripts.run_simulation --mode single --seed 0` failed (missing `.seed_artifacts/nmiai/medium`)
  - conversion acceptance:
    - `.seed_artifacts/experiments/branch_conversion_safe_targeted_retrieval_fork_v1/analysis_conversion_acceptance_smoke.json`
    - `.seed_artifacts/experiments/branch_conversion_safe_targeted_retrieval_fork_v1/analysis_conversion_acceptance_batch.json`
  - detailed batch analysis:
    - `.seed_artifacts/experiments/branch_conversion_safe_targeted_retrieval_fork_v1/analysis_detailed_batch.json`
- Live smoke check:
  - yes, exactly 1 run (`12 / 1 / 7`)
- Live batch size:
  - 3 runs (`3/0/3`, `10/1/5`, `11/1/6`)
- Artifact roots:
  - smoke: `.seed_artifacts/experiments/branch_conversion_safe_targeted_retrieval_fork_v1/smoke`
  - batch: `.seed_artifacts/experiments/branch_conversion_safe_targeted_retrieval_fork_v1/batch`

### Live Log Findings
- Score / orders / items:
  - batch median `10 / 1 / 5` vs baseline `88 / 9 / 43`
- Completion rounds:
  - first completion median `67`
  - no stable second completion
- Tail behavior:
  - `delivery_lane_force` activated too often (median `268` rounds)
  - sustain lane active almost entire run (`~300` rounds)
- Collision/queue/no-assignment findings:
  - `wait_due_to_no_assignment` stayed at `0` (assignment starvation solved in this family)
  - but congestion exploded:
    - `wait_due_to_collision_block` median `949`
    - `wait_due_to_drop_queue_slot_hold` median `566`
  - `wait_due_to_no_target` reduced vs old spatial collapse but still unstable (`59` median, smoke `1053`)
- Mechanism-specific telemetry evidence:
  - `active_sustain_targets` median `601`
  - `active_stage_targets` median `16`
  - `courier_active_targets` median `361`
  - mechanism executed, but conversion throughput collapsed.

### Regression Risks
- Main risks observed:
  - delivery-lane forcing is too aggressive and causes queue lock/collision inflation
  - sustain retrieval runs almost always, causing over-pull without conversion payoff
  - improving target-liveness alone does not produce score unless drop conversion is efficient
- What must be monitored next:
  - items-per-drop and drop-to-pick coupling first
  - queue-hold and collision waits under delivery-lane force
  - second/third completion recovery before any 10th-completion claims

### Verdict
- One of: accepted / rejected / candidate / revert_candidate
- rejected
- Why:
  - first real targeted-retrieval implementation avoided total collapse, but remained far below baseline and failed conversion acceptance gates (`overall_pass_rate=0.0`).

### Next Step
- Single next hypothesis:
  - keep targeted retrieval core, but weaken global delivery-lane force (make it conditional on local queue pressure) and cap sustain-lane admission to prevent queue-lock throughput collapse.
- Required evidence:
  - conversion gates pass on batch and median score/orders materially above this fork's `10 / 1 / 5`.

---

## 2026-03-11 / conversion_safe_targeted_retrieval_fork_v1_iter2

### Date / Iteration ID
- Date: 2026-03-11
- Iteration ID: conversion_safe_targeted_retrieval_fork_v1_iter2

### Branch
- Branch name: conversion_safe_targeted_retrieval_fork_v1
- Status: rejected (iter2 still below baseline)

### Hypothesis
- Bottleneck observed:
  - v1 over-forced delivery conversion globally, creating queue-lock/collision inflation.
- Why this bottleneck matters now:
  - family needs conversion guarantee without collapsing retrieval throughput.

### Intended Mechanism
- Expected behavior change:
  - force delivery for only one selected carrier bot (not all cargo carriers);
  - limit sustain lane to true commit-covered stall cases;
  - keep stage fallback and conservative commitment accounting.
- Why this should improve KPI:
  - reduce queue-lock while preserving conversion coupling.

### Smallest Patch
- Files touched:
  - `bot/expert_supply_strategy.py`
  - `tests/test_expert_supply_strategy.py`
  - `scripts/check_conversion_acceptance.py`
  - `BRANCH_SCORECARD.md`
  - `EXPERIMENT_JOURNAL.md`
- Scope boundary (what was intentionally not changed):
  - no legacy baseline changes
  - no guard actuator promotion/tuning
  - no broad planner-family rewrite

### Validation Performed
- Simulation/offline checks:
  - `python -m pytest tests/test_expert_supply_strategy.py -q` (21 passed)
  - `python -m pytest -q` (49 passed)
  - conversion acceptance:
    - `.seed_artifacts/experiments/branch_conversion_safe_targeted_retrieval_fork_v1_iter2/analysis_conversion_acceptance_smoke.json`
    - `.seed_artifacts/experiments/branch_conversion_safe_targeted_retrieval_fork_v1_iter2/analysis_conversion_acceptance_batch.json`
  - batch detail summary:
    - `.seed_artifacts/experiments/branch_conversion_safe_targeted_retrieval_fork_v1_iter2/analysis_detailed_batch.json`
- Live smoke check:
  - yes, exactly 1 run (`36 / 3 / 21`)
- Live batch size:
  - 3 runs (`34/3/19`, `32/3/17`, `31/3/16`)
- Artifact roots:
  - smoke: `.seed_artifacts/experiments/branch_conversion_safe_targeted_retrieval_fork_v1_iter2/smoke`
  - batch: `.seed_artifacts/experiments/branch_conversion_safe_targeted_retrieval_fork_v1_iter2/batch`

### Live Log Findings
- Score / orders / items:
  - batch median improved to `32 / 3 / 17` from iter1 `10 / 1 / 5`
  - still far below baseline `88 / 9 / 43`
- Completion rounds:
  - first/second/third median: `68 / 135 / 171`
  - better than prior collapse, but no high-order cadence regime
- Tail behavior:
  - delivery force became moderate (`delivery_lane_force` median `164` rounds vs 268 prior)
  - sustain lane reduced (`44` rounds vs ~300 prior)
- Collision/queue/no-assignment findings:
  - queue-hold dropped (`566 -> 73` median) and collisions reduced (`949 -> 480`)
  - no-assignment stayed `0`
  - but `wait_due_to_no_target` became dominant and severe (`1044` median)
- Mechanism-specific telemetry evidence:
  - stage fallback increased (`active_stage_targets` median `96`)
  - sustain targets reduced (`44` median)
  - conversion coupling improved (`drop_to_pick` ~`0.37`) but liveness gate still failed.

### Regression Risks
- Main risks observed:
  - target-liveness failure can hide inside moderate score gains
  - stage fallback still does not guarantee productive work when target eligibility is narrow
  - collision reduction can improve while strategic throughput remains too low
- What must be monitored next:
  - no-target ratio as hard gate
  - assignment usefulness while preserving conversion
  - whether branch can exceed 3-order regime before further expansion

### Verdict
- One of: accepted / rejected / candidate / revert_candidate
- rejected
- Why:
  - branch improved from collapse but still fails conversion acceptance (`target-liveness`) and remains far below baseline score regime.

### Next Step
- Single next hypothesis:
  - keep targeted retrieval family, but replace static stage fallback with guaranteed productive fallback missions (active-type shelf circulation with bounded overlap) to cut no-target starvation.
- Required evidence:
  - conversion gates pass on batch and median score materially above current `32 / 3 / 17`.
