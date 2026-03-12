# Mechanism

The candidate package described in `MAXSCORE_ENGINE_NOTES.md` is expected to improve Expert play by making the bot more assertive on high-value order chains while preserving delivery throughput under congestion.

Expected gains:

- better order-selection quality under the Expert order stream
- improved completed orders relative to the canonical default baseline
- fewer idle or no-assignment stalls in late-round congestion

Primary comparison target:

- `bot — копия/best/expert/current.json`

Candidate source:

- `expert_snapshot_copy_20260306_maxscore_engine/expert_snapshot_copy_20260306/artifacts/MAXSCORE_ENGINE_NOTES.md`
