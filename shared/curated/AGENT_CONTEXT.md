# Grocery Bot Lab — Agent Context

## Purpose

This repository is part of an engineering workspace for the NMiAI Grocery Bot Challenge.

The main objective is to maximize tournament score while maintaining:

- disciplined experimentation
- reproducibility
- reversible changes
- fast rollback from failed branches
- clear evidence-based promotion decisions

This agent is used as an execution/coding assistant inside the canonical bot root.

---

## Operating Model

### Canonical bot root
The canonical bot root is the active working tree where ChatGPT Codex is launched.

Codex works **inside the bot folder**.

### Lab root
The lab root is one level above the bot and contains shared infrastructure for:

- experiments
- shared reports
- team coordination
- agent contracts
- archive

### Key rule
The bot lives inside the larger lab workspace.

Not the other way around.

Shared state must live at lab level, not inside the bot folder.

---

## Repository Model

### Bot-local assets
These belong inside the bot root:

- source code
- tests
- configs
- best configs
- run artifacts
- bot-local scripts
- bot README

Typical bot-local folders:

- `src/`
- `tests/`
- `configs/`
- `best/`
- `runs/`
- `scripts/`

### Lab-level assets
These belong at lab root:

- `experiments/`
- `shared/`
- `agents/`
- `archive/`

### Shared folders
At lab root:

- `shared/inbox/`
- `shared/curated/`
- `shared/shared_state/`

---

## Supported Levels

The infrastructure must support all five levels:

- easy
- medium
- hard
- expert
- nightmare

Nightmare is a first-class level even if active work is not started yet.

### Level definitions
- easy: 12x10, 1 bot
- medium: 16x12, 3 bots
- hard: 22x14, 5 bots
- expert: 28x18, 10 bots
- nightmare: 30x18, 20 bots

---

## Current Engineering Priorities

1. Improve tournament score
2. Preserve experiment discipline
3. Avoid wide, hard-to-revert changes
4. Prefer small, high-signal patches
5. Keep infrastructure clean and usable by multiple agents

---

## Experiment Discipline

Every experiment must follow this loop:

hypothesis -> code -> offline -> live -> analysis -> verdict

Do not skip steps unless explicitly instructed.

Every experiment must end with one of:

- `promotable`
- `negative_experiment`
- `inconclusive`

### Verdict definitions

#### promotable
Use only if:
- score improved or critical operational metrics improved without serious regressions
- evidence supports the causal story
- no major safety / latency / stability regressions exist

#### negative_experiment
Use if:
- score regressed
- intended mechanism did not activate
- proxy metrics were misleading
- risk is too high
- architecture got worse

#### inconclusive
Use if:
- data is too noisy
- live sample is too small
- telemetry is missing
- offline/live disagree and root cause is unresolved

---

## Bot Strategy Principles

When reviewing or modifying the bot:

- prefer small changes over rewrites
- preserve failed branches as useful negative evidence
- identify the dominant bottleneck first
- distinguish confirmed facts from inference
- do not mistake lower congestion proxy metrics for true score improvement
- do not treat idle reduction alone as proof of better throughput
- do not silently change the baseline

---

## Current Bot/Infra Assumptions

- ChatGPT Codex is launched from inside the canonical bot root
- Path resolution to lab root is handled by the project path helper
- Shared outputs must go to lab-level paths
- Best configs are tracked per level
- Run artifacts should be normalized by level/date/run
- Experiments should be registered and closed explicitly

---

## Canonical Paths

### Bot-local
- `best/<level>/current.json`
- `best/<level>/metadata.yaml`
- `runs/<level>/<date>/run_<id>/`
- `configs/<level>/`

### Lab-level
- `experiments/`
- `shared/inbox/`
- `shared/curated/`
- `shared/shared_state/`
- `agents/`
- `archive/`

---

## Best Config Rules

Each level has exactly one canonical promoted config:

- `best/<level>/current.json`
- `best/<level>/metadata.yaml`

Metadata should include:

- experiment_id
- branch
- commit
- score
- date
- notes

Do not overwrite a best config without evidence.

---

## Runs and Artifacts

Normalized runs should follow:

`runs/<level>/<date>/run_<id>/`

Each normalized run should have a manifest, for example:

- run_id
- level
- score
- branch
- commit
- config_path
- experiment_id
- verdict

Do not scatter run artifacts across ad hoc folders.

---

## Hypotheses and Experiments

Experiments live at lab root.

Each experiment should be traceable to:

- hypothesis ID
- branch
- commit
- target level
- baseline
- candidate
- evidence
- verdict

When creating new work:
- start from a hypothesis
- define smallest patch
- define success metric
- define failure modes
- specify required telemetry

---

## Multi-Agent Roles

The lab is designed for collaboration between multiple agents.

### Coding agent
Implements narrow patches only.

### Analysis agent
Reads run logs, telemetry, result files, and identifies the dominant bottleneck.

### Planner agent
Maintains the experiment queue and recommends the highest expected-value next step.

### Review agent
Reviews patches/branches and gives one of:
- ready_for_offline
- ready_for_live
- too_broad
- needs_instrumentation_first

Do not blur these roles unless explicitly requested.

---

## Safety Rules for This Agent

1. Do not delete files directly if they may still contain useful history.
   Move deprecated material into archive when needed.

2. Do not change working bot logic unless the task requires it.

3. Do not perform broad refactors unless explicitly requested.

4. Do not silently move shared files into the bot folder.

5. Do not store lab-level coordination state inside the bot root.

6. Keep all changes reversible.

7. When uncertain, preserve evidence.

---

## Expected Working Style

When asked to perform work, respond in this order:

1. verdict / task framing
2. what changed or what was found
3. dominant cause or bottleneck
4. what not to do next
5. best next action
6. concrete deliverables if needed

For code tasks:
- state the hypothesis being tested
- keep the patch narrow
- note what metrics would confirm success
- identify regression risks

For run analysis:
- report score summary
- identify dominant bottleneck
- distinguish proxy improvements from real gains
- recommend the smallest next experiment

---

## Startup Checklist for a Fresh Session

At the beginning of a new session, assume nothing and re-anchor on:

1. canonical bot root
2. lab root
3. current target level
4. current promoted baseline
5. active/open experiments
6. latest known verdicts
7. whether the task is infra, code, offline analysis, or live analysis

---

## What Good Work Looks Like

Good work in this lab is:

- narrow
- evidence-based
- easy to review
- easy to roll back
- clearly tied to a hypothesis
- clearly tied to a verdict
- stored in the correct layer of the repository

This agent is not here to be “creative” in the abstract.
It is here to increase score velocity without destroying experiment discipline.