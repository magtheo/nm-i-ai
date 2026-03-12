"""Branch-aware evolution loop for bot_v.3.

This extends the Forge orchestrator from bot_v.2 with champion-registry
integration.  The key difference from the plain orchestrator is:

1. **Champion protection** — a candidate is only promoted when it beats the
   current champion by at least ``min_improvement``.  The old champion is
   always archived to history (never deleted).

2. **Hypothesis tracking** — every candidate that runs gets a
   ``HypothesisRecord`` so you can audit the full experiment history.

3. **No Codex dependency** — this module runs the sim-only loop.  If you
   have Codex CLI, plug it in via the ``codex_command`` parameter just like
   the v.2 orchestrator.

4. **Safety gate** — if a candidate has any simulator errors it is
   automatically archived, protecting the champion.

Usage (simulation-only, no Codex)
----------------------------------
::

    from forge.evolution import run_hypothesis

    result = run_hypothesis(
        hypothesis_name="preview_lookahead_v1",
        candidate_strategy="forge/hypotheses/preview_v1.py",
        description="Add 2-step preview lookahead",
    )
    print(result.status)   # "promoted" or "archived"

Usage (full evolution loop with Codex)
--------------------------------------
::

    from forge.evolution import run_evolution_loop, EvolutionLoopConfig
    run_evolution_loop(EvolutionLoopConfig(...))
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from bot.champion import (
    ChampionRecord,
    champion_score,
    describe_champion,
    is_better_than_champion,
    load_champion,
    set_champion,
)
from bot.hypothesis import HypothesisManager, HypothesisRecord
from forge.simulator import DEFAULT_DIFFICULTY_SEEDS, evaluate_strategy_file, BatchEvaluation


ROOT = Path(__file__).resolve().parents[1]


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EvolutionLoopConfig:
    """Configuration for the branch-aware evolution loop."""

    strategy_file: Path = field(default_factory=lambda: ROOT / "forge" / "strategy.py")
    """Active champion strategy — NEVER overwritten unless a candidate beats it."""

    candidate_file: Path = field(default_factory=lambda: ROOT / "forge" / "strategy_candidate.py")
    """Temporary candidate file written by the loop before evaluation."""

    hypotheses_dir: Path = field(default_factory=lambda: ROOT / "forge" / "hypotheses")
    """Directory where individual hypothesis strategy files live."""

    run_root: Path = field(default_factory=lambda: ROOT / ".forge_runs")
    """Root directory for per-iteration artefacts."""

    codex_command: str = "codex generate --prompt-file {prompt_file}"
    """Command template for Codex CLI; set to '' to disable LLM mutation."""

    iterations: int = 1
    """Number of mutation+eval cycles. 0 = infinite."""

    max_rounds: int = 300
    """Rounds per simulation run."""

    strict_actions: bool = True
    max_fix_attempts: int = 2

    min_improvement: float = 0.0
    """Candidate must beat champion by at least this many points to be promoted."""

    sleep_sec: float = 0.0
    difficulty_seeds: dict[str, int] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation helpers (wrap forge.simulator)
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate_file(
    strategy_file: Path,
    *,
    max_rounds: int = 300,
    strict_actions: bool = True,
    difficulty_seeds: dict[str, int] | None = None,
) -> BatchEvaluation:
    seeds = dict(DEFAULT_DIFFICULTY_SEEDS)
    if difficulty_seeds:
        seeds.update(difficulty_seeds)
    return evaluate_strategy_file(
        strategy_file=strategy_file,
        difficulties=["easy", "medium", "hard", "expert"],
        difficulty_seeds=seeds,
        max_rounds=max_rounds,
        strict_actions=strict_actions,
    )


def _batch_to_per_difficulty(batch: BatchEvaluation) -> dict[str, float]:
    return {run.difficulty: float(run.score) for run in batch.runs}


# ─────────────────────────────────────────────────────────────────────────────
# Single-hypothesis evaluation + promotion
# ─────────────────────────────────────────────────────────────────────────────

def run_hypothesis(
    hypothesis_name: str,
    candidate_strategy: str | Path,
    *,
    description: str = "",
    max_rounds: int = 300,
    strict_actions: bool = True,
    difficulty_seeds: dict[str, int] | None = None,
    min_improvement: float = 0.0,
    verbose: bool = True,
) -> HypothesisRecord:
    """Evaluate a single hypothesis strategy and attempt to promote it.

    This is the main entry point for testing a new idea safely:

    1. Register the hypothesis in the registry
    2. Evaluate it in simulation
    3. Compare to current champion
    4. Promote (update champion) if it wins, archive otherwise
    5. Current champion is NEVER modified if candidate doesn't win

    Parameters
    ----------
    hypothesis_name:
        Unique slug for this experiment, e.g. ``"preview_lookahead_v2"``.
    candidate_strategy:
        Path to the candidate strategy .py file.
    """
    mgr = HypothesisManager()
    candidate_path = Path(candidate_strategy).resolve()

    if verbose:
        print(f"\n{'='*70}")
        print(f"[evolution] Evaluating hypothesis: {hypothesis_name!r}")
        print(f"[evolution] Strategy file: {candidate_path}")
        print(f"[evolution] {describe_champion()}")
        print(f"{'='*70}")

    # Register (or update if already exists)
    if mgr._path(hypothesis_name).exists():
        record = mgr.load(hypothesis_name)
        record.strategy_file = str(candidate_path)
        record.description = description or record.description
        record.status = "proposed"
        mgr.save(record)
    else:
        mgr.propose(
            hypothesis_name,
            str(candidate_path),
            description=description,
        )

    mgr.mark_evaluating(hypothesis_name)

    # Evaluate
    batch: BatchEvaluation | None = None
    error: Optional[str] = None
    try:
        if not candidate_path.exists():
            raise FileNotFoundError(f"Candidate strategy not found: {candidate_path}")
        batch = _evaluate_file(
            candidate_path,
            max_rounds=max_rounds,
            strict_actions=strict_actions,
            difficulty_seeds=difficulty_seeds,
        )
        if batch.has_errors():
            error = batch.worst_error() or "Simulator error"
    except Exception as exc:
        error = str(exc)

    avg_score = batch.average_score() if batch is not None else 0.0
    per_diff = _batch_to_per_difficulty(batch) if batch is not None else {}
    current_champ_score = champion_score()

    record = mgr.record_evaluation(
        hypothesis_name,
        average_score=avg_score,
        per_difficulty=per_diff,
        champion_score_at_eval=current_champ_score,
        error=error,
    )

    if verbose:
        print(f"[evolution] Candidate score:  {avg_score:.2f}")
        print(f"[evolution] Champion score:   {current_champ_score:.2f}")
        if per_diff:
            for diff, sc in sorted(per_diff.items()):
                print(f"            {diff:<12}: {sc:.0f}")
        if error:
            print(f"[evolution] ERROR: {error}")

    # Gate: promote only if no errors and score beats champion
    if error:
        record = mgr.archive(hypothesis_name, reason=f"evaluation error: {error}")
        if verbose:
            print(f"[evolution] → ARCHIVED (error)")
        return record

    promoted = mgr.try_promote(hypothesis_name, min_improvement=min_improvement)

    if verbose:
        if promoted:
            print(f"[evolution] → PROMOTED  ✓  (new champion: {avg_score:.2f})")
        else:
            print(f"[evolution] → ARCHIVED  ✗  (did not beat champion by {min_improvement})")

    return mgr.load(hypothesis_name)


# ─────────────────────────────────────────────────────────────────────────────
# Seed champion from a strategy file
# ─────────────────────────────────────────────────────────────────────────────

def seed_champion_from_file(
    strategy_file: str | Path,
    hypothesis_name: str = "initial_seed",
    *,
    max_rounds: int = 300,
    strict_actions: bool = True,
    difficulty_seeds: dict[str, int] | None = None,
    force: bool = True,
    verbose: bool = True,
) -> ChampionRecord:
    """Evaluate *strategy_file* and set it as the champion unconditionally.

    Use this once to establish the initial champion baseline.  After that,
    use ``run_hypothesis()`` for all experiments — only challengers that
    beat the established baseline will take over.
    """
    strategy_path = Path(strategy_file).resolve()
    if verbose:
        print(f"[evolution] Seeding champion from {strategy_path}…")

    batch = _evaluate_file(
        strategy_path,
        max_rounds=max_rounds,
        strict_actions=strict_actions,
        difficulty_seeds=difficulty_seeds,
    )
    avg = batch.average_score()
    per_diff = _batch_to_per_difficulty(batch)

    record = ChampionRecord(
        strategy_file=str(strategy_path),
        average_score=avg,
        per_difficulty=per_diff,
        promoted_at=datetime.now().isoformat(timespec="seconds"),
        hypothesis_name=hypothesis_name,
    )
    set_champion(record, force=force)

    if verbose:
        print(f"[evolution] Champion seeded: avg_score={avg:.2f}")
        for d, s in sorted(per_diff.items()):
            print(f"            {d:<12}: {s:.0f}")

    return record


# ─────────────────────────────────────────────────────────────────────────────
# Codex helpers (same as v.2 orchestrator, reused here)
# ─────────────────────────────────────────────────────────────────────────────

def _quote_arg(value: str | Path) -> str:
    text = str(value)
    return '"' + text.replace('"', '\\"') + '"'


def _extract_python_code(raw: str) -> str:
    text = raw.strip()
    fence = "```"
    if fence not in text:
        return text
    parts = text.split(fence)
    blocks = [parts[i] for i in range(1, len(parts), 2)]
    for block in blocks:
        stripped = block.strip()
        if stripped.startswith("python"):
            return stripped[len("python"):].lstrip("\n")
    return blocks[0].lstrip("\n") if blocks else text


def _run_codex_once(*, prompt_file: Path, command_template: str, timeout_sec: int = 300) -> str:
    template = command_template.strip()
    quoted = _quote_arg(prompt_file)
    if "{prompt_file}" in template:
        command = template.format(prompt_file=quoted)
    else:
        command = f"{template} --prompt-file {quoted}"
    proc = subprocess.run(
        command, shell=True, text=True, capture_output=True, timeout=timeout_sec
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        raise RuntimeError(f"Codex command failed: {stderr or stdout or proc.returncode}")
    output = (proc.stdout or "").strip()
    if not output:
        raise RuntimeError("Codex command returned empty output")
    return _extract_python_code(output)


def _ensure_python_compiles(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Mutation prompt (same logic as v.2 orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

ALLOWED_ACTIONS_TEXT = "move_up, move_down, move_left, move_right, pick_up, drop_off, wait"


def _describe_bottleneck(batch: BatchEvaluation) -> str:
    if batch.has_errors():
        return f"Execution error detected: {batch.worst_error()}"
    idle = batch.aggregate_idle_ratio()
    worst_bot = max(idle, key=lambda k: idle[k]) if idle else None
    worst_ratio = idle.get(worst_bot, 0.0) if worst_bot is not None else 0.0
    blocked_avg = sum(r.blocked_moves for r in batch.runs) / len(batch.runs) if batch.runs else 0
    collision_avg = sum(r.collision_blocks for r in batch.runs) / len(batch.runs) if batch.runs else 0
    if worst_bot is not None and worst_ratio >= 0.35:
        return f"Bot {worst_bot} idles too much ({worst_ratio:.1%}). Improve task assignment."
    if blocked_avg > 40:
        return f"High blocked-move rate ({blocked_avg:.1f}/run). Reduce congestion."
    if collision_avg > 10:
        return f"Frequent collision blocks ({collision_avg:.1f}/run). Spread bots across shelves."
    return "Increase score by improving active-order completion speed while preserving protocol correctness."


def _build_mutation_prompt(*, strategy_source: str, baseline_summary: dict, bottleneck: str) -> str:
    return "\n".join([
        "You are mutating a Grocery Bot strategy module.",
        "",
        "HARD CONSTRAINTS:",
        "1) Keep function signature exactly: decide_intents(game_state: dict[str, Any]) -> list[dict[str, Any]].",
        "2) Output only valid Python source code (no markdown).",
        f"3) Do NOT invent protocol actions. Allowed: {ALLOWED_ACTIONS_TEXT}.",
        "4) You may only edit strategy logic, no external dependencies.",
        "5) Keep code deterministic and safe under missing keys.",
        "",
        "Current baseline summary:",
        json.dumps(baseline_summary, indent=2),
        "",
        "Identified bottleneck:",
        bottleneck,
        "",
        "Mutation goal: improve active-order completion speed. "
        "If active needs are covered, allow free bots to prefetch preview items "
        "without overfilling inventory.",
        "",
        "Current strategy.py:",
        "<<PYTHON",
        strategy_source.rstrip(),
        "PYTHON",
        "",
    ])


def _build_fix_prompt(*, strategy_source: str, error_text: str) -> str:
    return "\n".join([
        "Fix this strategy.py so it runs correctly in simulator.",
        "",
        "HARD CONSTRAINTS:",
        f"- Allowed actions only: {ALLOWED_ACTIONS_TEXT}",
        "- Output only Python code, no markdown.",
        "- Keep function signature: decide_intents(game_state: dict[str, Any]) -> list[dict[str, Any]]",
        "",
        f"Error: {error_text}",
        "",
        "Current candidate code:",
        "<<PYTHON",
        strategy_source.rstrip(),
        "PYTHON",
        "",
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Full Codex-driven evolution loop
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IterationOutcome:
    iteration: int
    promoted: bool
    baseline_score: float
    candidate_score: Optional[float]
    error: Optional[str]
    run_dir: Path
    hypothesis_name: str


def run_evolution_loop(config: EvolutionLoopConfig) -> list[IterationOutcome]:
    """Branch-aware Codex evolution loop.

    Same algorithm as bot_v.2's orchestrator but with champion-registry
    gating: a candidate is only promoted if it beats the current champion
    (not just the iteration-local baseline).
    """
    if not config.codex_command:
        raise RuntimeError("codex_command is empty. Use run_hypothesis() for sim-only evaluation.")

    config.run_root.mkdir(parents=True, exist_ok=True)
    strategy_file = config.strategy_file.resolve()
    candidate_file = config.candidate_file.resolve()
    mgr = HypothesisManager()

    # Establish baseline
    baseline_batch = _evaluate_file(
        strategy_file,
        max_rounds=config.max_rounds,
        strict_actions=config.strict_actions,
        difficulty_seeds=config.difficulty_seeds,
    )
    baseline_score = baseline_batch.average_score()

    # Seed champion if not set yet
    champ = load_champion()
    if champ is None:
        champ = ChampionRecord(
            strategy_file=str(strategy_file),
            average_score=baseline_score,
            per_difficulty=_batch_to_per_difficulty(baseline_batch),
            promoted_at=datetime.now().isoformat(timespec="seconds"),
            hypothesis_name="initial_seed",
        )
        set_champion(champ, force=True)

    outcomes: list[IterationOutcome] = []
    iteration = 0

    while True:
        iteration += 1
        if config.iterations > 0 and iteration > config.iterations:
            break

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = config.run_root / f"iter_{iteration:04d}_{ts}"
        run_dir.mkdir(parents=True, exist_ok=True)
        hypothesis_name = f"codex_iter_{iteration:04d}_{ts}"

        strategy_source = strategy_file.read_text(encoding="utf-8")
        bottleneck = _describe_bottleneck(baseline_batch)
        baseline_summary = {
            "average_score": baseline_batch.average_score(),
            "runs": [
                {"difficulty": r.difficulty, "score": r.score,
                 "items_delivered": r.items_delivered,
                 "orders_completed": r.orders_completed}
                for r in baseline_batch.runs
            ],
        }

        prompt_text = _build_mutation_prompt(
            strategy_source=strategy_source,
            baseline_summary=baseline_summary,
            bottleneck=bottleneck,
        )
        prompt_file = run_dir / "mutation_prompt.txt"
        _write_text(prompt_file, prompt_text)

        error_text: Optional[str] = None
        candidate_batch: BatchEvaluation | None = None
        candidate_score: Optional[float] = None

        for attempt_idx in range(config.max_fix_attempts + 1):
            try:
                if attempt_idx == 0:
                    code = _run_codex_once(
                        prompt_file=prompt_file,
                        command_template=config.codex_command,
                    )
                else:
                    fix_prompt = _build_fix_prompt(
                        strategy_source=candidate_file.read_text(encoding="utf-8"),
                        error_text=error_text or "Unknown error",
                    )
                    fix_file = run_dir / f"fix_prompt_{attempt_idx}.txt"
                    _write_text(fix_file, fix_prompt)
                    code = _run_codex_once(
                        prompt_file=fix_file,
                        command_template=config.codex_command,
                    )
            except Exception as exc:
                error_text = str(exc)
                if attempt_idx >= config.max_fix_attempts:
                    break
                continue

            _write_text(candidate_file, code.strip() + "\n")

            try:
                _ensure_python_compiles(candidate_file)
            except Exception as exc:
                error_text = f"Syntax error: {exc}"
                if attempt_idx >= config.max_fix_attempts:
                    break
                continue

            candidate_batch = _evaluate_file(
                candidate_file,
                max_rounds=config.max_rounds,
                strict_actions=config.strict_actions,
                difficulty_seeds=config.difficulty_seeds,
            )
            candidate_score = candidate_batch.average_score()

            if candidate_batch.has_errors():
                error_text = candidate_batch.worst_error() or "Candidate runtime error"
                if attempt_idx >= config.max_fix_attempts:
                    break
                continue

            error_text = None
            break

        # Register hypothesis record
        mgr.propose(hypothesis_name, str(candidate_file), description=bottleneck, overwrite=True)
        mgr.mark_evaluating(hypothesis_name)
        mgr.record_evaluation(
            hypothesis_name,
            average_score=candidate_score or 0.0,
            per_difficulty=_batch_to_per_difficulty(candidate_batch) if candidate_batch else {},
            champion_score_at_eval=champion_score(),
            error=error_text,
        )

        # Promotion gate: must beat current champion (not just iteration baseline)
        promoted = False
        if candidate_batch is not None and candidate_score is not None and error_text is None:
            if is_better_than_champion(candidate_score, min_improvement=config.min_improvement):
                # Update strategy file (makes it the new live champion code)
                strategy_file.write_text(
                    candidate_file.read_text(encoding="utf-8"), encoding="utf-8"
                )
                mgr.try_promote(hypothesis_name, min_improvement=config.min_improvement)
                baseline_batch = candidate_batch
                baseline_score = candidate_score
                promoted = True
            else:
                mgr.archive(hypothesis_name, reason="did not beat champion")

        # Save iteration report
        report = {
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "champion_score": champion_score(),
            "promoted": promoted,
            "error": error_text,
            "bottleneck": bottleneck,
        }
        _write_text(run_dir / "iteration_report.json", json.dumps(report, indent=2))

        outcomes.append(IterationOutcome(
            iteration=iteration,
            promoted=promoted,
            baseline_score=baseline_score,
            candidate_score=candidate_score,
            error=error_text,
            run_dir=run_dir,
            hypothesis_name=hypothesis_name,
        ))

        print(
            f"[evolution] iter={iteration:4d}  candidate={candidate_score or 0:.2f}"
            f"  champion={champion_score():.2f}  promoted={promoted}"
            + (f"  error={error_text[:60]}" if error_text else "")
        )

        if config.sleep_sec > 0:
            time.sleep(config.sleep_sec)

    return outcomes
