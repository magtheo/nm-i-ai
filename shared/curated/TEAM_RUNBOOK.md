# Grocery Bot Lab — Team Runbook

## Purpose

This document explains how teammates should use the current lab/bot system.

It is intended for engineers and collaborators working on the Grocery Bot project with ChatGPT Codex and the shared lab infrastructure.

---

## High-Level Model

There are two layers:

### 1. Bot layer
This is the canonical bot working tree.

This is where:
- code is edited
- Codex is launched
- configs live
- best configs live
- normalized runs for this bot live

### 2. Lab layer
This is the shared engineering workspace above the bot.

This is where:
- experiments are tracked
- reports are stored
- shared state is stored
- agent coordination lives
- deprecated artifacts are archived

### Core rule
Launch Codex inside the bot root, but keep shared coordination data at lab level.

---

## Folder Responsibilities

### Bot-local folders
Use these inside the bot root:

- `src/` — bot code
- `tests/` — tests
- `configs/` — configs by level
- `best/` — promoted best configs by level
- `runs/` — normalized run artifacts for this bot
- `scripts/` — bot-local automation

### Lab-level folders
Use these at lab root:

- `experiments/` — hypotheses and verdicts
- `shared/inbox/` — raw outputs and handoffs
- `shared/curated/` — dashboards, summaries, reviews
- `shared/shared_state/` — active baselines and priorities
- `agents/` — agent briefs and contracts
- `archive/` — deprecated or migrated artifacts

---

## Supported Levels

The system supports:

- easy
- medium
- hard
- expert
- nightmare

Nightmare must remain present in the structure even if work has not started yet.

---

## Standard Working Cycle

Every experiment should follow:

1. define hypothesis
2. create narrow patch
3. run offline or replay screening
4. run live if justified
5. analyze results
6. assign verdict

Do not skip the verdict step.

Allowed verdicts:
- promotable
- negative_experiment
- inconclusive

---

## How To Start a New Session

### Human teammate checklist
Before asking Codex to work, confirm:

- which bot root is canonical
- which level you are targeting
- what the current promoted baseline is
- which experiment is active
- whether the task is:
  - infrastructure
  - code patch
  - offline analysis
  - live run review
  - strategy/planning

### Recommended first prompt to Codex
Tell it:
- what level to work on
- current baseline
- what experiment or hypothesis to continue
- whether it should analyze, code, or organize
- any constraints on scope

If available, also give it the saved `AGENT_CONTEXT.md`.

---

## How To Launch Codex

Launch Codex from inside the canonical bot root.

That allows it to:
- work directly on bot code
- use bot-local scripts
- resolve lab-level paths through the project path helper

Do not launch it from a random parent directory unless you know the path model still resolves correctly.

---

## Best Config Workflow

Each level has one canonical promoted config:

- `best/<level>/current.json`
- `best/<level>/metadata.yaml`

### When to update a best config
Only update when:
- the experiment verdict is promotable
- live evidence supports the promotion
- metadata is ready

### Metadata should include
- experiment_id
- branch
- commit
- date
- score
- notes

Do not manually create multiple competing “best” configs.

---

## Run Artifact Workflow

Normalized runs should live under:

`runs/<level>/<date>/run_<id>/`

Each run should include enough information to understand:
- what level it belongs to
- which config was used
- what score it got
- what experiment it supports

Prefer normalized paths over ad hoc replay folders.

---

## Experiment Workflow

### Creating an experiment
Every experiment should start from a hypothesis.

A good hypothesis includes:
- mechanism
- smallest patch
- success metric
- failure modes
- required telemetry

### Closing an experiment
Every experiment must end with:
- promotable
- negative_experiment
- inconclusive

Do not leave experiments half-open without a clear conclusion.

---

## Shared State Workflow

The team should keep these files current:

### `shared/shared_state/active_baselines.yaml`
Tracks current promoted baselines per level.

### `shared/shared_state/current_priorities.yaml`
Tracks current engineering priorities.

### `shared/shared_state/open_experiments.yaml`
Tracks experiments that are still active.

### `shared/shared_state/owners.yaml`
Tracks who owns which role.

### `shared/shared_state/promotion_rules.yaml`
Tracks promotion logic and operational rules.

These files are the coordination layer for both humans and AI agents.

---

## Multi-Agent Workflow

The system supports multiple agent roles.

### Coding agent
Use for:
- narrow implementation patches
- instrumentation
- safe refactors
- tests

### Analysis agent
Use for:
- replay analysis
- run log analysis
- telemetry interpretation
- bottleneck identification

### Planner agent
Use for:
- prioritization
- experiment queue design
- next-step recommendation

### Review agent
Use for:
- branch review
- patch scope review
- readiness verdicts

### Practical rule
Do not ask one agent to do all roles at once unless necessary.

Specialization improves signal quality.

---

## Infrastructure Scripts

Typical scripts include:

- cleanup artifacts
- normalize runs
- promote best config
- create new hypothesis
- close experiment

### Recommended usage pattern
1. run dry mode first
2. inspect output
3. apply changes only after review

Especially for:
- cleanup
- migration
- normalization

---

## Cleanup Rules

Never delete uncertain files immediately.

If something looks deprecated:
- review first
- archive it if needed
- preserve reversibility

Examples of likely cleanup candidates:
- duplicate configs
- stale debug dumps
- abandoned snapshot copies
- orphan artifacts not tied to any baseline or verdict

---

## What To Do After a Live Run

After each live run:

1. store the run artifacts
2. normalize the run location if needed
3. update experiment notes
4. write a short analysis summary
5. decide whether the run supports:
   - promotion
   - rejection
   - inconclusive status

Do not rely on memory.

Write it down in the system.

---

## Common Mistakes To Avoid

### Mistake 1
Putting shared coordination files inside the bot root.

### Mistake 2
Updating the best config without metadata or verdict.

### Mistake 3
Running broad experiments without a hypothesis.

### Mistake 4
Trusting proxy metrics more than score.

### Mistake 5
Leaving negative experiments undocumented.

### Mistake 6
Creating duplicate “current best” files.

### Mistake 7
Letting Codex write outside the intended bot/lab structure without review.

---

## Recommended Team Conventions

### Naming
Use stable IDs for:
- hypotheses
- experiments
- verdicts

### Scope
Prefer small patches over rewrites.

### Reporting
Keep summaries concise and operational:
- verdict
- evidence
- root cause
- best next action

### Promotion
Promotion requires evidence, not optimism.

---

## Minimum End-to-End Workflow Example

1. teammate launches Codex inside bot root
2. Codex reads `AGENT_CONTEXT.md`
3. Codex creates or continues a hypothesis
4. Codex implements a narrow patch
5. Codex runs offline checks
6. Codex prepares or executes a live run
7. Codex stores outputs in correct bot/lab locations
8. Codex writes summary and verdict
9. teammate reviews whether to promote or rollback

---

## Definition of a Healthy System

The system is working correctly if:

- Codex can start from a fresh session and recover context quickly
- all five levels exist in the structure
- best configs are canonical and easy to find
- run artifacts are normalized and easy to inspect
- experiments are traceable
- shared state is readable by both humans and AI agents
- failed experiments remain useful, not lost
- new teammates can join without reverse-engineering the repo

---

## Final Note

This system is designed to improve engineering velocity without sacrificing discipline.

The goal is not just to make the bot better.
The goal is to make the whole team better at improving the bot.