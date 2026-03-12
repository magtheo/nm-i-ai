# Mechanism

Describe why this should help.
Expert maps run with 10 bots on a large grid, so the active order is often under-covered while some bots fall back to `idle` because the baseline leaves anti-no-assignment support disabled.

This candidate enables already-implemented support behavior:

- `anti_no_assignment_enabled = true`
- `secondary_assignment_enabled = true`
- `secondary_reposition_empty_only = false`

Expected effect:

- fewer pure `idle_fallback` assignments when active items are only partially covered
- more bots repositioned toward useful secondary support locations
- lower idle-step accumulation in under-covered active-order states

What this does **not** prove by itself:

- direct score improvement in real Expert sessions
- improvement under all congestion patterns
