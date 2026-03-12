# LIVE_BUDGET_POLICY

This policy is mandatory for AI experiment execution in this repository.

## 1) Smoke Rule

- Use exactly **1 live run** for smoke check when needed.
- Purpose of smoke run:
  - bot starts correctly
  - actions are valid
  - artifacts are written

## 2) Batch Rule

- Use at most **3 live runs per hypothesis batch**.
- Do not exceed 3 runs in a single comparison batch.

## 3) Rate Awareness

- Platform limit: **40 live runs per hour**.
- Operate conservatively and preserve live budget.

## 4) Sim/Offline First

- Prefer simulation and artifact analysis before new live runs.
- Live runs are for hypothesis confirmation, not exploration by brute force.

## 5) Prohibited Waste Patterns

Do not spend live budget on:
- broad speculative rewrites
- multi-mechanism unscoped changes
- repeated retries without new hypothesis evidence
- "score fishing" from tiny samples

## 6) Required Live Usage Statement

Every serious report must include:
- whether new live runs were used
- how many
- why they were necessary

## 7) Escalation

If evidence is ambiguous:
1. add minimal telemetry,
2. run offline analysis,
3. then run a bounded live batch (max 3).

Do not escalate live volume first.
