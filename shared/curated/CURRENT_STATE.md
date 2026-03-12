# Grocery Bot Lab — Current State

## Status Snapshot

This file is the short operational state summary for fresh sessions.

Use it together with:
- `AGENT_CONTEXT.md`
- `TEAM_RUNBOOK.md`
- `baseline_policy_expert.md`

This document should be updated whenever the active experiment direction changes.

---

## Canonical Layout

### Canonical bot root
The canonical bot root is the active Codex working tree.

Codex is launched from inside the canonical bot root.

### Lab root
The lab root is the shared workspace above the bot root.

Lab-level coordination artifacts live there:
- `experiments/`
- `shared/`
- `agents/`
- `archive/`

### Source of truth
The repository / lab structure is the source of truth.

If chat summaries and repo state differ, repo state wins.

---

## Current Target Level

### Primary active level
`expert`

### Other levels
- easy: supported, not current focus
- medium: supported
- hard: supported
- nightmare: first-class in infrastructure, not yet an active optimization track

---

## Current Expert Baseline Status

### Current baseline classification
- baseline_status: operational_bootstrap
- provenance_status: unresolved
- comparison_allowed: yes
- historical_authority: no

### Meaning
The current Expert baseline is valid for:
- current experiment comparisons
- baseline registration
- workflow execution
- near-term promotion / rejection decisions

The current Expert baseline is **not** a fully verified historical baseline for the original stable Expert 82 profile.

### Provenance note
Exact recovery of the real run-backed stable Expert 82 config was not possible from the current workspace.

The active Expert baseline currently exists as a transparent bootstrap reference and should remain usable until a stronger run-backed baseline is recovered.

Historical baseline recovery is a parallel, non-blocking task.

---

## Best Known Expert Performance Signal

### Stable ceiling observed
`82`

### Important note
This is the best stable known Expert performance signal discussed in current working context.

However, the exact original run-backed config for that historical stable 82 profile is currently not recovered inside the active workspace.

---

## Recent Expert Experiment Verdicts

### Delivered-gate + stall-breaker
- verdict: `negative_experiment`
- reason:
  - average score delta vs baseline was only marginal
  - strong regressions in idle
  - strong regressions in wait_due_to_no_assignment
- interpretation:
  - the mechanism increased starvation and reduced useful assignment pressure

### Stall-breaker only
- verdict: `negative_experiment`
- reason:
  - severe collapse in score
- interpretation:
  - broad/global stall-breaker behavior is harmful in current architecture

### Delivered-gate only
- verdict: `inconclusive`
- reason:
  - local positive signal exists
  - evidence is too weak for promotion
- interpretation:
  - not the main problem, but not yet promotable

### Active-duplicates only
- verdict: `promotable` as stable baseline candidate behavior
- result:
  - stable `82 / 82 / 82`
- interpretation:
  - safe and stable within current architecture
  - does not break through the current Expert ceiling

---

## Current Dominant Bottleneck

### Best current reading
The dominant Expert bottleneck is still:

`multi-bot congestion + starvation under weak assignment recovery`

### More specific interpretation
Not all low-level movement improvements translate into score.

The main failure mode is:
- bots lose useful assignments
- bots fall into idle / no-assignment states
- local anti-stall mechanisms become harmful if triggered too broadly

### Operational takeaway
Avoid broad fallback logic that reduces useful assignment pressure.

---

## What Not To Do Next

Do **not**:

- reintroduce a global or frequent stall-breaker
- treat lower congestion proxies as proof of better score
- do broad orchestration rewrites
- block current work waiting for perfect baseline provenance recovery
- promote anything without clear evidence above or beyond the current ceiling behavior
- mix infrastructure work with algorithmic changes in the same patch unless absolutely necessary

---

## Best Next Experiment

### Recommended next direction
Run a **narrow Expert experiment** against the current operational bootstrap baseline.

### Best current candidate
`late-only anti-starvation / secondary recovery support`

### Hypothesis shape
A very narrow late-game fallback may help only in true near-end deadlock situations, without causing the broad starvation regressions seen in earlier stall-breaker experiments.

### Required constraints
- narrow patch only
- offline/replay check first
- no broad refactor
- no infrastructure changes mixed into the patch
- honest verdict after evidence

---

## Current Operational Policy

### Repo / lab truth
Keep canonical state in repo/lab structure.

### Codex role
Codex is the execution agent inside the bot root.

### ChatGPT Business role
ChatGPT Business Project is the continuity and coordination layer, not the canonical storage layer.

### Custom GPT role
Use role-specific GPTs for:
- coding
- analysis
- planning
- review

Do not let custom GPTs become the source of truth.

---

## Immediate Working Priorities

1. Use the current stabilized layout in real work
2. Run the next narrow Expert experiment through the full workflow
3. Preserve experiment discipline
4. Keep shared summaries updated
5. Recover the historical Expert 82 baseline only as a parallel task

---

## Fresh Session Instructions

At the beginning of a fresh session, re-anchor on:

1. canonical bot root
2. lab root
3. current target level = expert
4. current Expert baseline = operational_bootstrap
5. recent negative verdicts on stall-breaker variants
6. current ceiling signal = 82
7. next preferred experiment = narrow late-only anti-starvation style patch

---

## Update Rule

Update this file when any of the following changes:
- active target level
- promoted baseline
- dominant bottleneck
- recent verdict stack
- next best experiment