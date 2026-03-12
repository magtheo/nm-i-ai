"""Evolution orchestrator for automated strategy mutation with Codex CLI."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .simulator import BatchEvaluation, evaluate_strategy_file


ALLOWED_ACTIONS_TEXT = "move_up, move_down, move_left, move_right, pick_up, drop_off, wait"


@dataclass
class EvolutionConfig:
    strategy_file: Path
    candidate_file: Path
    run_root: Path
    codex_command: str
    iterations: int
    max_rounds: int
    strict_actions: bool
    max_fix_attempts: int
    auto_commit: bool
    sleep_between_iterations_sec: float
    difficulty_seeds: dict[str, int]


@dataclass
class IterationOutcome:
    iteration: int
    promoted: bool
    baseline_score: float
    candidate_score: float | None
    error: str | None
    run_dir: Path


def _quote_arg(value: str | Path) -> str:
    text = str(value)
    return '"' + text.replace('"', '\\"') + '"'


def _ensure_python_compiles(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")


def _extract_python_code(raw: str) -> str:
    text = raw.strip()
    if not text:
        return text

    fence = "```"
    if fence not in text:
        return text

    parts = text.split(fence)
    blocks: list[str] = []
    for idx in range(1, len(parts), 2):
        blocks.append(parts[idx])

    for block in blocks:
        stripped = block.strip()
        if stripped.startswith("python"):
            return stripped[len("python") :].lstrip("\n")

    if blocks:
        return blocks[0].lstrip("\n")

    return text


def _run_codex_once(*, prompt_file: Path, command_template: str, timeout_sec: int = 300) -> str:
    template = command_template.strip()
    quoted_prompt = _quote_arg(prompt_file)

    if "{prompt_file}" in template:
        command = template.format(prompt_file=quoted_prompt)
    else:
        command = f"{template} --prompt-file {quoted_prompt}"

    proc = subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True,
        timeout=timeout_sec,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        hint = stderr or stdout or f"codex exited with code {proc.returncode}"
        raise RuntimeError(f"Codex command failed: {hint}")

    output = (proc.stdout or "").strip()
    if not output:
        raise RuntimeError("Codex command returned empty output")
    return _extract_python_code(output)


def _summarize_evaluation(eval_result: BatchEvaluation) -> dict[str, Any]:
    runs = eval_result.to_dict().get("runs", [])
    compact_runs = []
    for run in runs:
        compact_runs.append(
            {
                "difficulty": run.get("difficulty"),
                "seed": run.get("seed"),
                "score": run.get("score"),
                "items_delivered": run.get("items_delivered"),
                "orders_completed": run.get("orders_completed"),
                "blocked_moves": run.get("blocked_moves"),
                "collision_blocks": run.get("collision_blocks"),
                "idle_ratio_by_bot": run.get("idle_ratio_by_bot"),
                "error": run.get("error"),
            }
        )

    return {
        "average_score": eval_result.average_score(),
        "has_errors": eval_result.has_errors(),
        "worst_error": eval_result.worst_error(),
        "aggregate_idle_ratio": eval_result.aggregate_idle_ratio(),
        "runs": compact_runs,
    }


def _describe_bottleneck(eval_result: BatchEvaluation) -> str:
    if eval_result.has_errors():
        return f"Execution error detected: {eval_result.worst_error()}"

    idle = eval_result.aggregate_idle_ratio()
    worst_bot = None
    worst_ratio = -1.0
    for bot_id, ratio in idle.items():
        if ratio > worst_ratio:
            worst_ratio = ratio
            worst_bot = bot_id

    blocked_avg = 0.0
    collision_avg = 0.0
    if eval_result.runs:
        blocked_avg = sum(run.blocked_moves for run in eval_result.runs) / len(eval_result.runs)
        collision_avg = sum(run.collision_blocks for run in eval_result.runs) / len(eval_result.runs)

    if worst_bot is not None and worst_ratio >= 0.35:
        return (
            f"Bot {worst_bot} idles too much ({worst_ratio:.1%} average). "
            "Improve task assignment and reduce wait-heavy behavior."
        )
    if blocked_avg > 40:
        return (
            f"High blocked-move rate ({blocked_avg:.1f} per run). "
            "Reduce congestion and avoid bots targeting the same pickup lanes."
        )
    if collision_avg > 10:
        return (
            f"Frequent collision blocks ({collision_avg:.1f} per run). "
            "Distribute bots across different shelves and reduce route overlap."
        )

    return "Increase score by improving active-order completion speed while preserving protocol correctness."


def _build_mutation_prompt(
    *,
    strategy_source: str,
    baseline_summary: dict[str, Any],
    bottleneck: str,
    extra_instruction: str,
) -> str:
    body = [
        "You are mutating a Grocery Bot strategy module.",
        "",
        "HARD CONSTRAINTS:",
        "1) Keep function signature exactly: decide_intents(game_state: dict[str, Any]) -> list[dict[str, Any]].",
        "2) Output only valid Python source code (no markdown).",
        f"3) Do NOT invent protocol actions. Allowed actions: {ALLOWED_ACTIONS_TEXT}.",
        "4) You may only edit strategy logic, no external dependencies.",
        "5) Keep code deterministic and safe under missing keys.",
        "",
        "Current baseline summary:",
        json.dumps(baseline_summary, indent=2, ensure_ascii=True),
        "",
        "Identified bottleneck:",
        bottleneck,
        "",
        "Mutation goal:",
        extra_instruction,
        "",
        "Current strategy.py:",
        "<<PYTHON",
        strategy_source.rstrip(),
        "PYTHON",
        "",
    ]
    return "\n".join(body)


def _build_fix_prompt(*, strategy_source: str, error_text: str) -> str:
    body = [
        "Fix this strategy.py so it runs correctly in simulator.",
        "",
        "HARD CONSTRAINTS:",
        "- Keep function signature: decide_intents(game_state: dict[str, Any]) -> list[dict[str, Any]]",
        f"- Allowed actions only: {ALLOWED_ACTIONS_TEXT}",
        "- Output only Python code, no markdown.",
        "",
        "Error:",
        error_text,
        "",
        "Current candidate code:",
        "<<PYTHON",
        strategy_source.rstrip(),
        "PYTHON",
        "",
    ]
    return "\n".join(body)


def _evaluate(
    *,
    strategy_file: Path,
    max_rounds: int,
    strict_actions: bool,
    difficulty_seeds: dict[str, int],
) -> BatchEvaluation:
    return evaluate_strategy_file(
        strategy_file=strategy_file,
        difficulties=["easy", "medium", "hard", "expert"],
        difficulty_seeds=difficulty_seeds,
        max_rounds=max_rounds,
        strict_actions=strict_actions,
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _promote_candidate(*, candidate_file: Path, strategy_file: Path) -> None:
    strategy_file.write_text(candidate_file.read_text(encoding="utf-8"), encoding="utf-8")


def _git_commit_if_enabled(
    *,
    enabled: bool,
    repo_root: Path,
    strategy_file: Path,
    score_before: float,
    score_after: float,
) -> str | None:
    if not enabled:
        return None

    inside = subprocess.run(
        "git rev-parse --is-inside-work-tree",
        shell=True,
        text=True,
        capture_output=True,
        cwd=repo_root,
    )
    if inside.returncode != 0:
        return "git commit skipped (not a git repository)"

    rel_path = strategy_file.as_posix()
    add_cmd = subprocess.run(
        f"git add {_quote_arg(rel_path)}",
        shell=True,
        text=True,
        capture_output=True,
        cwd=repo_root,
    )
    if add_cmd.returncode != 0:
        return f"git add failed: {(add_cmd.stderr or add_cmd.stdout).strip()}"

    msg = f"forge: promote strategy ({score_before:.2f} -> {score_after:.2f})"
    commit_cmd = subprocess.run(
        f"git commit -m {_quote_arg(msg)}",
        shell=True,
        text=True,
        capture_output=True,
        cwd=repo_root,
    )
    if commit_cmd.returncode != 0:
        detail = (commit_cmd.stderr or commit_cmd.stdout).strip()
        if "nothing to commit" in detail.lower():
            return "git commit skipped (nothing to commit)"
        return f"git commit failed: {detail}"
    return (commit_cmd.stdout or "").strip() or "git commit created"


def run_evolution_loop(config: EvolutionConfig) -> list[IterationOutcome]:
    config.run_root.mkdir(parents=True, exist_ok=True)
    strategy_file = config.strategy_file.resolve()
    candidate_file = config.candidate_file.resolve()

    baseline_eval = _evaluate(
        strategy_file=strategy_file,
        max_rounds=config.max_rounds,
        strict_actions=config.strict_actions,
        difficulty_seeds=config.difficulty_seeds,
    )
    baseline_score = baseline_eval.average_score()

    outcomes: list[IterationOutcome] = []
    iteration = 0

    while True:
        iteration += 1
        if config.iterations > 0 and iteration > config.iterations:
            break

        run_dir = config.run_root / f"iter_{iteration:04d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        run_dir.mkdir(parents=True, exist_ok=True)

        strategy_source = strategy_file.read_text(encoding="utf-8")
        baseline_summary = _summarize_evaluation(baseline_eval)
        bottleneck = _describe_bottleneck(baseline_eval)

        prompt_text = _build_mutation_prompt(
            strategy_source=strategy_source,
            baseline_summary=baseline_summary,
            bottleneck=bottleneck,
            extra_instruction=(
                "Your current algorithm should account for preview orders: if active-order needs are already "
                "covered by assigned bots, let free bots prefetch useful preview items without overfilling inventory."
            ),
        )
        prompt_file = run_dir / "mutation_prompt.txt"
        _write_text(prompt_file, prompt_text)

        error_text: str | None = None
        candidate_eval: BatchEvaluation | None = None
        candidate_score: float | None = None

        for attempt_idx in range(config.max_fix_attempts + 1):
            if attempt_idx == 0:
                code = _run_codex_once(prompt_file=prompt_file, command_template=config.codex_command)
            else:
                fix_prompt = _build_fix_prompt(
                    strategy_source=candidate_file.read_text(encoding="utf-8"),
                    error_text=error_text or "Unknown error",
                )
                fix_file = run_dir / f"fix_prompt_{attempt_idx}.txt"
                _write_text(fix_file, fix_prompt)
                code = _run_codex_once(prompt_file=fix_file, command_template=config.codex_command)

            _write_text(candidate_file, code.strip() + "\n")

            try:
                _ensure_python_compiles(candidate_file)
            except Exception as exc:  # noqa: BLE001
                error_text = f"Syntax/compile error: {exc}"
                if attempt_idx >= config.max_fix_attempts:
                    break
                continue

            candidate_eval = _evaluate(
                strategy_file=candidate_file,
                max_rounds=config.max_rounds,
                strict_actions=config.strict_actions,
                difficulty_seeds=config.difficulty_seeds,
            )
            candidate_score = candidate_eval.average_score()

            if candidate_eval.has_errors():
                error_text = candidate_eval.worst_error() or "Candidate runtime error"
                if attempt_idx >= config.max_fix_attempts:
                    break
                continue

            error_text = None
            break

        promoted = False
        git_note = None
        if candidate_eval is not None and candidate_score is not None and error_text is None:
            if candidate_score > baseline_score:
                _promote_candidate(candidate_file=candidate_file, strategy_file=strategy_file)
                git_note = _git_commit_if_enabled(
                    enabled=config.auto_commit,
                    repo_root=strategy_file.parent,
                    strategy_file=strategy_file,
                    score_before=baseline_score,
                    score_after=candidate_score,
                )
                baseline_eval = candidate_eval
                baseline_score = candidate_score
                promoted = True

        report = {
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "promoted": promoted,
            "error": error_text,
            "bottleneck": bottleneck,
            "baseline": _summarize_evaluation(baseline_eval),
            "candidate": _summarize_evaluation(candidate_eval) if candidate_eval is not None else None,
            "git": git_note,
        }
        _write_text(run_dir / "iteration_report.json", json.dumps(report, indent=2, ensure_ascii=True))

        outcomes.append(
            IterationOutcome(
                iteration=iteration,
                promoted=promoted,
                baseline_score=baseline_score,
                candidate_score=candidate_score,
                error=error_text,
                run_dir=run_dir,
            )
        )

        if config.sleep_between_iterations_sec > 0:
            time.sleep(config.sleep_between_iterations_sec)

    return outcomes


def _parse_seed_overrides(raw: str) -> dict[str, int]:
    out: dict[str, int] = {}
    if not raw.strip():
        return out
    for token in raw.split(","):
        part = token.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError("Seed overrides must use difficulty=seed format")
        key, value = part.split("=", 1)
        out[key.strip().lower()] = int(value.strip())
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Automated Forge evolution loop")
    parser.add_argument("--strategy-file", type=str, default="forge/strategy.py")
    parser.add_argument("--candidate-file", type=str, default="forge/strategy_candidate.py")
    parser.add_argument("--run-root", type=str, default=".forge_runs")
    parser.add_argument(
        "--codex-command",
        type=str,
        default="codex generate --prompt-file {prompt_file}",
        help="Command template; must print candidate Python to stdout. Uses {prompt_file} placeholder.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="0 = infinite loop, otherwise fixed iteration count",
    )
    parser.add_argument("--max-rounds", type=int, default=300)
    parser.add_argument("--max-fix-attempts", type=int, default=2)
    parser.add_argument("--non-strict", action="store_true")
    parser.add_argument("--auto-commit", action="store_true")
    parser.add_argument("--sleep-sec", type=float, default=0.0)
    parser.add_argument("--seed-overrides", type=str, default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = EvolutionConfig(
        strategy_file=Path(args.strategy_file),
        candidate_file=Path(args.candidate_file),
        run_root=Path(args.run_root),
        codex_command=str(args.codex_command),
        iterations=int(args.iterations),
        max_rounds=int(args.max_rounds),
        strict_actions=not bool(args.non_strict),
        max_fix_attempts=max(0, int(args.max_fix_attempts)),
        auto_commit=bool(args.auto_commit),
        sleep_between_iterations_sec=max(0.0, float(args.sleep_sec)),
        difficulty_seeds=_parse_seed_overrides(str(args.seed_overrides)),
    )

    outcomes = run_evolution_loop(config)
    payload = {
        "iterations": len(outcomes),
        "outcomes": [
            {
                "iteration": row.iteration,
                "promoted": row.promoted,
                "baseline_score": row.baseline_score,
                "candidate_score": row.candidate_score,
                "error": row.error,
                "run_dir": str(row.run_dir),
            }
            for row in outcomes
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
