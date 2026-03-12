# Mechanism

This experiment targets the known Expert failure mode:

- multi-bot congestion with weak assignment recovery
- bots falling into no-assignment or idle states
- broad recovery logic becoming harmful when active too often

The proposed mechanism is intentionally narrow.

Instead of enabling secondary recovery globally, the patch should only allow the existing secondary support path to activate when both conditions hold:

- the run is in a true late-game window
- the bot has already accumulated sustained assignment starvation

Expected effect:

- fewer late no-assignment deadlocks
- less broad interference with useful primary assignment pressure
- safer recovery than prior global stall-breaker style behavior

This experiment does not assume that lower idle alone is success.
Score safety still decides the verdict.
