# Expert Baseline Policy

## Status

The current Expert baseline is valid as an **operational baseline** but not yet as a fully verified historical baseline.

## Current classification

- baseline_status: operational_bootstrap
- provenance_status: unresolved
- comparison_allowed: yes
- historical_authority: no

## Meaning

The current Expert baseline may be used for:

- current experiment comparisons
- active baseline registration
- workflow execution
- promotion / rejection decisions for near-term experiments

The current Expert baseline must **not** be treated as:

- the authoritative historical source for the stable Expert 82 profile
- proof of the exact original config used in the best known historical run

## Reason

Exact recovery of the real run-backed stable Expert 82 configuration was not possible from the current workspace.

The current baseline was derived from available local defaults and was preserved transparently with provenance notes.

## Operational rule

Until a real run-backed Expert baseline is recovered:

1. use the current baseline for active engineering work
2. keep provenance notes attached
3. do not claim historical equivalence
4. preserve all future promoted baseline history
5. treat baseline recovery as a parallel, non-blocking task

## Promotion rule

A future Expert config may replace the current operational bootstrap baseline if:

- it is supported by stronger live evidence, or
- it is a recovered run-backed historical baseline with better provenance

## Recovery rule

Historical baseline recovery remains a separate task and should search:

- old branches
- external workspace copies
- archived best-config files
- run artifacts
- live reports
- team notes

## Team guidance

Do not block current experiments waiting for perfect provenance.
Use the current baseline honestly and continue the experiment loop.