# Grocery Bot Lab — Current Issues and Tasks

> **Last updated:** 2026-03-12
> **Based on:** BRANCH_SCORECARD.md, CURRENT_STATE.md, EXPERIMENT_JOURNAL.md, and full system analysis

---

## Summary

This document identifies the current weaknesses of the Grocery Bot system and lists concrete tasks ordered by priority.
Issues are grouped into four categories:

1. **Performance bottlenecks** — algorithmic ceiling problems
2. **Architecture and design weaknesses** — structural issues in bot design
3. **Infrastructure and tooling gaps** — missing or fragile operational tooling
4. **Process and documentation gaps** — workflow and knowledge-management risks

---

## 1. Performance Bottlenecks

### 1.1 Expert Score Ceiling at ~82–88

**Description:**
The Expert map score is stuck in the 82–88 range (median). The 10th order completion has not been
unlocked reliably. Every attempted mechanism to push past this ceiling has either failed or produced
inconclusive results.

**Root cause analysis:**
- The dominant bottleneck is **multi-bot congestion combined with weak assignment recovery**.
- When bots lose their assignments (e.g. after a collision or a re-route), they fall into
  `idle` or `wait_due_to_no_assignment` states and do not recover quickly.
- The current assignment recovery logic is too weak: it does not reactivate stalled bots fast
  enough in late-game rounds when the order buffer is critically thin.
- Broad fallback mechanisms (stall-breakers, pipeline budgets, task pools) consistently make
  this worse by diverting bot capacity away from productive throughput.

**Evidence:**
- `stall-breaker only`: severe score collapse (negative_experiment)
- `delivered-gate + stall-breaker`: marginal delta, strong regressions in idle and no-assignment
- `pipeline_budget_fork_v1`: hard gates suppressed pre-pick, score dropped to median 70
- `task_pool_fork_v1`: over-admission of active-close tasks exploded no-assignment, score 54

**Tasks:**
- [ ] **T-1.1.a** Design and test a **narrow, late-only anti-starvation recovery** mechanism
  that activates only in true near-end deadlock situations (e.g. round > 250 and
  `wait_due_to_no_assignment` > threshold), without affecting mid-game throughput.
- [ ] **T-1.1.b** Instrument and measure `wait_due_to_no_assignment` and `idle` counts
  per phase (early / mid / late) to identify exactly when and where starvation peaks.
- [ ] **T-1.1.c** Investigate whether the current **orbit admission policy** contributes to
  starvation by holding bots in orbit too long after delivery.

---

### 1.2 9th Order Completion Comes Too Late (~round 282)

**Description:**
The 9th order completion consistently arrives around round 282 (out of 300). This leaves
insufficient rounds to attempt a 10th completion. Any mechanism that delays the 9th
completion further is catastrophic for score.

**Root cause analysis:**
- Late-game bots accumulate congestion near the drop-off zone.
- The delivery queue lacks a priority escalation for near-complete orders.
- Preview pre-picking sometimes over-allocates bots to future orders while the 9th active
  order is still short of closure.

**Tasks:**
- [ ] **T-1.2.a** Add a **delivery-priority escalation mode**: when an order needs only
  1–2 more items to complete and it is past round 240, temporarily increase the delivery
  concurrency cap for that order.
- [ ] **T-1.2.b** Limit preview pre-pick budget allocation in late-game rounds (after round 220)
  to avoid starving the active near-complete order.
- [ ] **T-1.2.c** Track 9th-completion round explicitly in telemetry and conversion gate output
  to enable regression monitoring across runs.

---

### 1.3 Baseline Provenance Unresolved

**Description:**
The current Expert baseline (`bundle_a_starvation_relief_empty_only`, median 88) is classified
as `operational_bootstrap`. The original run-backed stable Expert 82 config has not been
recovered from the workspace.

**Impact:**
- Comparison authority is limited; historical baselines cannot be verified.
- Regressions that occurred before the current workspace was set up are invisible.
- Future experiments are anchored to a bootstrap reference rather than a fully verified baseline.

**Tasks:**
- [ ] **T-1.3.a** Recover or re-run the original stable Expert 82 configuration and register
  it as a fully verified historical baseline in `shared/shared_state/canonical_bot.yaml`.
- [ ] **T-1.3.b** Run a 3-run validation batch with the recovered config and confirm
  it passes all conversion acceptance gates.
- [ ] **T-1.3.c** Update `BRANCH_SCORECARD.md` and `CURRENT_STATE.md` with the verified
  provenance status once resolved.

---

### 1.4 Non-Expert Difficulty Levels Underoptimised

**Description:**
Easy, Medium, and Hard levels are "supported" but not actively optimised. Leaderboard scoring
is the sum across all four maps, meaning any underperformance on non-Expert maps reduces the
total competitive score.

**Tasks:**
- [ ] **T-1.4.a** Run a dedicated 3-run baseline batch for Medium and Hard to establish
  current performance signals.
- [ ] **T-1.4.b** Identify if the canonical Expert preset regresses on Medium/Hard due to
  Expert-specific tuning (orbit flow engine, 10-bot assignment depth).
- [ ] **T-1.4.c** Create separate optimised presets for Medium and Hard and register them
  in the branch scorecard.

---

### 1.5 Nightmare Level Not Yet Active

**Description:**
The infrastructure supports a `nightmare` difficulty level but no optimisation work has been
done. This is a potential future competitive score source.

**Tasks:**
- [ ] **T-1.5.a** Confirm that the game server actually exposes a `nightmare` difficulty
  and document its grid/bot/order parameters in `GROCERY_BOT_PROTOCOL.md`.
- [ ] **T-1.5.b** Once confirmed, create a dedicated `nightmare` preset and run baseline
  smoke + batch evaluation.

---

## 2. Architecture and Design Weaknesses

### 2.1 Conversion Acceptance Gates Not Automated in CI

**Description:**
Conversion acceptance gates are the primary correctness guard for every branch, but they must
be run **manually** after each batch. There is no automated enforcement that prevents
promoting a branch that fails the gates.

**Tasks:**
- [ ] **T-2.1.a** Integrate `check_conversion_acceptance.py` into the pytest suite as a
  parametrised test that runs against stored `.seed_artifacts` reference runs.
- [ ] **T-2.1.b** Add a pre-commit or pre-promotion checklist hook that errors if a batch
  artifact does not include a passing gate check output.

---

### 2.2 Emergency Actuator Has No Safe Promotion Path

**Description:**
The conversion guard emergency actuator was built as an R&D mechanism but has no defined
path to production. It is frozen but not removed. This creates dead code in the main
decision engine path and confusion about what is and is not active.

**Tasks:**
- [ ] **T-2.2.a** Formally decide: either define a narrow activation contract for the
  actuator (trigger condition + acceptance test) or remove it from the main code path
  and archive it in a separate experimental module.
- [ ] **T-2.2.b** Ensure the conversion guard telemetry fields are retained (they are
  valuable diagnostics) regardless of what happens to the actuator.

---

### 2.3 Orbit Flow Engine Is Expert-Only and Not Testable via Main Simulator

**Description:**
The orbit flow engine (`orbit_flow_engine.py`) is a specialised component for Expert-only
behaviour. It is not exercised by the general simulation harness and only partially covered
by `test_orbit_wall_conveyor.py`.

**Tasks:**
- [ ] **T-2.3.a** Add parametric test cases that simulate orbit admission, pick sortie
  allocation, and return-to-orbit with controlled `game_state` inputs.
- [ ] **T-2.3.b** Add orbit metrics (orbit queue depth, sortie count per round, return
  latency) to the telemetry output to enable post-run analysis.

---

### 2.4 Forge Strategy Module Is Disconnected from Bot Core

**Description:**
The Forge system (`forge/strategy.py`) uses a simplified `decide_intents()` interface that
is isolated from the full bot decision engine. Strategies evolved in the Forge cannot be
directly transplanted into `bot/decision_engine.py` without manual adaptation.

**Tasks:**
- [ ] **T-2.4.a** Define a canonical mapping from Forge `decide_intents()` output to
  `bot/decision_engine.py` intent format, enabling partial strategy migration.
- [ ] **T-2.4.b** Add a validation step in the Forge orchestrator that compares Forge
  simulation score against main simulator score for the same strategy, to catch
  simulator divergence early.

---

### 2.5 No Explicit Role Separation for Easy / Medium Maps

**Description:**
The orbit flow engine and expert supply strategy are Expert-only. For Easy and Medium maps,
role assignment falls back to the general greedy/auction assignment engine without any
map-specific tuning. With 1–5 bots on smaller grids, the multi-bot assumptions may introduce
unnecessary overhead.

**Tasks:**
- [ ] **T-2.5.a** Audit `decision_engine.py` to verify that single-bot (Easy) and 3-bot
  (Medium) paths are exercised correctly and do not carry Expert-specific overhead.
- [ ] **T-2.5.b** Add targeted regression tests for Easy and Medium map scenarios using
  the offline simulator.

---

### 2.6 No Deadlock Recovery for Two-Bot Corridor Standoffs

**Description:**
`cooperative_path.py` resolves multi-bot conflict components, but there is no explicit
recovery for the common two-bot head-on corridor deadlock where both bots are committed
to moving into the other's current cell.

**Tasks:**
- [ ] **T-2.6.a** Add a test case to `test_collision.py` that reproduces a head-on
  two-bot corridor deadlock and verifies that the cooperative path planner resolves it
  within 2 ticks.
- [ ] **T-2.6.b** If the test reveals a failure, implement a priority-yield rule: the
  lower-priority bot (by bot ID) yields for one tick to break the deadlock symmetry.

---

## 3. Infrastructure and Tooling Gaps

### 3.1 No Automated Regression Baseline

**Description:**
There is no automated process that re-runs the canonical baseline on a schedule and alerts
if the median score drops below a threshold. Regressions can be introduced by parameter
drift or accidental file edits and go undetected until a live batch is run.

**Tasks:**
- [ ] **T-3.1.a** Create a `scripts/run_regression_baseline.sh` wrapper that runs the
  canonical preset in simulation, checks the score, and prints a PASS/FAIL result.
- [ ] **T-3.1.b** Add a pytest fixture that runs the canonical preset in the offline
  simulator and asserts score ≥ threshold (configurable via `pytest.ini`).

---

### 3.2 Artifact Retention Policy Is Undefined

**Description:**
Run artifacts accumulate in `.seed_artifacts/` with no defined retention or archiving policy.
Over time this will consume significant disk space and make it harder to locate reference runs.

**Tasks:**
- [ ] **T-3.2.a** Define a retention policy: keep the last N runs per branch, archive
  older runs to a dated subfolder or compress them.
- [ ] **T-3.2.b** Add `.seed_artifacts/` pruning guidance to `LIVE_BUDGET_POLICY.md`.

---

### 3.3 No Cross-Difficulty Score Tracking Dashboard

**Description:**
The branch scorecard tracks only Expert scores. Easy, Medium, and Hard scores are not
systematically tracked, which means changes that improve Expert but regress other maps
would go unnoticed.

**Tasks:**
- [ ] **T-3.3.a** Extend `BRANCH_SCORECARD.md` to include columns for Easy, Medium, Hard,
  and Expert median scores, plus a total leaderboard projection.
- [ ] **T-3.3.b** Add a `compare_runs.py` flag `--all-difficulties` that runs a comparison
  batch across all four difficulty presets and prints a consolidated summary table.

---

### 3.4 `.env` File Not Gitignored

**Description:**
A live `.env` file is present at `bot_v.2/.env`. If this contains a real `AINM_ACCESS_TOKEN`,
committing it would expose credentials.

**Tasks:**
- [ ] **T-3.4.a** Verify that `bot_v.2/.env` is listed in `.gitignore` (root and/or
  `bot_v.2/`-level) and that the live token has never been committed to git history.
- [ ] **T-3.4.b** If the token was ever committed, rotate it immediately and add a
  pre-commit hook to block `.env` files.

---

### 3.5 Forge Evolution Loop Has No Score Regression Guard

**Description:**
The Forge orchestrator promotes a new strategy if it scores higher than the current baseline.
However, it does not check conversion acceptance gates. A Forge-evolved strategy could
achieve a higher raw score while breaking conversion coupling, making it unsuitable for
promotion to the main bot.

**Tasks:**
- [ ] **T-3.5.a** Add a post-evaluation conversion gate check inside `forge/orchestrator.py`
  as a promotion prerequisite.
- [ ] **T-3.5.b** Log gate pass/fail results in Forge run output files for auditability.

---

## 4. Process and Documentation Gaps

### 4.1 EXPERIMENT_JOURNAL.md Has No Closed-Loop Update Rule

**Description:**
The journal template is well-defined, but there is no enforced process that ensures every
live batch produces a journal entry. Sessions that end without a written verdict contribute
to knowledge drift across AI agent sessions.

**Tasks:**
- [ ] **T-4.1.a** Add a mandatory pre-close checklist to `AI_EXPERIMENT_WORKFLOW.md`:
  "Before ending a session, write a journal entry for every live batch run."
- [ ] **T-4.1.b** Ensure that each session ends with an updated `CURRENT_STATE.md` that
  reflects the most recent verdict, even if it is `inconclusive`.

---

### 4.2 Agent Handoff Packages Are Not Systematically Created

**Description:**
The `shared/inbox/` directory is defined as the handoff location between agents, but there
is no documented process for when and how handoff packages are created. This means agents
can start sessions without full context from the previous session.

**Tasks:**
- [ ] **T-4.2.a** Define a handoff package template in `shared/inbox/HANDOFF_TEMPLATE.md`
  that includes: last verdict, current baseline config, open hypotheses, blockers.
- [ ] **T-4.2.b** Make handoff package creation a mandatory end-of-session step in
  `AI_EXPERIMENT_WORKFLOW.md`.

---

### 4.3 Branch Scorecard Does Not Track Conversion Gate Results

**Description:**
The scorecard records raw scores but not conversion gate pass/fail status. A branch with
a high score but failed gates looks indistinguishable from a fully healthy branch in the
scorecard.

**Tasks:**
- [ ] **T-4.3.a** Add a `Gates Passed` column to `BRANCH_SCORECARD.md` with values
  `all`, `partial (N/6)`, or `failed`.
- [ ] **T-4.3.b** Retroactively fill in gate status for all existing branches from
  available artifact data.

---

### 4.4 No Documented Rollback Procedure

**Description:**
When a branch experiment is rejected, there is no explicit step-by-step rollback procedure
documented. This can cause confusion about which config files to revert and which artifact
roots to preserve for reference.

**Tasks:**
- [ ] **T-4.4.a** Document a rollback procedure in `AI_EXPERIMENT_WORKFLOW.md`:
  1. Identify files changed (from journal entry)
  2. Revert to canonical preset
  3. Tag the rejected run artifacts with a `rejected/` prefix
  4. Update branch status to `deprecated` in scorecard

---

### 4.5 Protocol Documentation May Be Stale

**Description:**
`GROCERY_BOT_PROTOCOL.md` was last updated 2026-03-03. The game protocol (MCP docs server)
may have changed since then, especially as the competition end date (Mar 16, 2026) approaches.

**Tasks:**
- [ ] **T-4.5.a** Re-fetch the protocol from `https://mcp-docs.ainm.no/mcp` (nmiai-challenge)
  and diff against the current `GROCERY_BOT_PROTOCOL.md`.
- [ ] **T-4.5.b** Update the protocol document and bump the `Last updated` date if any
  changes are found.

---

## Priority Matrix

| ID | Issue | Impact | Effort | Priority |
|----|-------|--------|--------|----------|
| T-1.1.a | Narrow late-only anti-starvation mechanism | High | Medium | **P1** |
| T-1.2.a | Delivery-priority escalation for near-complete orders | High | Low | **P1** |
| T-3.4.a | Verify `.env` is gitignored (credentials risk) | Critical | Low | **P1** |
| T-1.1.b | Instrument no-assignment per phase | High | Low | **P2** |
| T-1.2.b | Limit preview pre-pick in late game | High | Low | **P2** |
| T-1.3.a | Recover verified Expert 82 baseline | High | Medium | **P2** |
| T-2.1.a | Automate conversion gates in pytest | Medium | Medium | **P2** |
| T-3.5.a | Add conversion gate check to Forge orchestrator | Medium | Low | **P2** |
| T-1.2.c | Track 9th-completion round in telemetry | Medium | Low | **P3** |
| T-1.4.a | Baseline batch for Medium and Hard | Medium | Low | **P3** |
| T-3.3.a | Cross-difficulty score tracking in scorecard | Medium | Low | **P3** |
| T-4.3.a | Add gate status column to branch scorecard | Low | Low | **P3** |
| T-2.6.a | Test + fix two-bot corridor deadlock | Medium | Medium | **P3** |
| T-2.4.a | Forge-to-core strategy migration mapping | Low | High | **P4** |
| T-1.5.a | Nightmare level activation | Low | Medium | **P4** |
| T-3.1.a | Automated regression baseline script | Medium | Low | **P4** |

---

## What Not to Do (Anti-Patterns Confirmed by Evidence)

The following approaches have been tried and empirically proven harmful. Do not retry these
without a fundamentally different mechanism:

- ❌ **Global / broad stall-breaker** — always increases no-assignment starvation in the current architecture
- ❌ **Hard pipeline budget gates** — suppress pre-pick work and collapse throughput
- ❌ **Soft pipeline budget penalties** — still raise no-assignment despite softer application
- ❌ **Task pool admission over-allocation** — depletes fallback and preload bots, explodes no-assignment
- ❌ **Spatial role partitioning without orbit flow** — near-zero pick-to-drop conversion
- ❌ **Multi-mechanism rewrites in a single patch** — too many confounds to diagnose regressions
- ❌ **Score fishing from single runs** — high variance; use 3-run medians with gate checks
