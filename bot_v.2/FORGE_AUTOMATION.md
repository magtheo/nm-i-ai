# Automated Forge

Automated Forge is an isolated optimization loop for Grocery Bot with 4 modules:

1. Local Simulator (`forge/simulator.py`)
- deterministic map generation by `(difficulty, seed)`
- protocol-level action resolution in bot-ID order
- strict validator for invalid actions
- score model: `+1 item`, `+5 order`

2. Bot Core (`forge/core.py`, `forge/pathfinding.py`)
- immutable runtime shell around strategy
- `game_state` parsing, A* navigation, and JSON action payload rendering
- optional live WebSocket runner (`LiveCoreRunner`)

3. Strategy Module (`forge/strategy.py`)
- mutation-only zone
- required interface:
```python
# must stay unchanged

def decide_intents(game_state: dict[str, Any]) -> list[dict[str, Any]]:
    ...
```

4. Orchestrator (`forge/orchestrator.py`)
- baseline evaluation on `easy/medium/hard/expert`
- automatic prompt assembly with score + bottleneck + errors
- Codex CLI candidate generation
- verification + fix loop for syntax/runtime/protocol failures
- promote-or-rollback decision

## Commands

Run simulator once:

```bash
python -m scripts.run_forge_simulation --strategy-file forge/strategy.py --output .forge_runs/smoke.json
```

Run one evolution iteration:

```bash
python -m scripts.run_forge_evolution --iterations 1
```

Run infinite loop:

```bash
python -m scripts.run_forge_evolution --iterations 0
```

Custom Codex command template:

```bash
python -m scripts.run_forge_evolution \
  --codex-command "codex generate --prompt-file {prompt_file}" \
  --iterations 5
```

## Strict action guard

If strategy emits any action not in protocol section 7, simulator fails immediately with:

`Invalid action generated. Allowed actions: move_up, move_down, move_left, move_right, pick_up, drop_off, wait`

This message is automatically routed into orchestrator fix prompts.
