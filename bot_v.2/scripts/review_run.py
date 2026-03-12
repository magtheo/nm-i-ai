"""Review one run: compute analyzer output and optionally generate replay HTML."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .orbit_wall_log_analyzer import analyze as analyze_run_trace
    from .render_live_ui import _build_html, _load_frames, _load_json
except ImportError:  # pragma: no cover - direct script execution fallback
    from orbit_wall_log_analyzer import analyze as analyze_run_trace
    from render_live_ui import _build_html, _load_frames, _load_json


def _latest_run_dir(artifact_root: Path, difficulty: str) -> Path:
    root = artifact_root
    difficulty_dir = artifact_root / difficulty
    if difficulty_dir.exists():
        root = difficulty_dir
    if not root.exists():
        raise RuntimeError(f"Artifact root not found: {root}")
    runs = [p for p in root.iterdir() if p.is_dir() and p.name.startswith("run_")]
    if not runs:
        raise RuntimeError(f"No run_* directories found under {root}")
    runs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for run_dir in runs:
        if (run_dir / "decision_trace.jsonl").exists():
            return run_dir
    raise RuntimeError(f"No run with decision_trace.jsonl found under {root}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze and render a live run artifact")
    parser.add_argument("--artifact-dir", type=str, default="", help="Explicit run_YYYYMMDD_HHMMSS directory")
    parser.add_argument("--artifact-root", type=str, default=".seed_artifacts/nmiai", help="Root containing runs")
    parser.add_argument("--difficulty", type=str, default="expert", help="Difficulty subdir under artifact root")
    parser.add_argument("--analysis-output", type=str, default="", help="Output path for analysis JSON")
    parser.add_argument("--no-render", action="store_true", help="Skip replay HTML generation")
    parser.add_argument("--render-output", type=str, default="", help="Output path for replay HTML")
    parser.add_argument("--title", type=str, default="NMiAI Replay", help="Replay page title")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if str(args.artifact_dir).strip():
        run_dir = Path(args.artifact_dir).resolve()
    else:
        run_dir = _latest_run_dir(
            Path(args.artifact_root).resolve(),
            str(args.difficulty).strip().lower(),
        )

    if not run_dir.exists():
        raise SystemExit(f"Run directory not found: {run_dir}")

    decision_trace = run_dir / "decision_trace.jsonl"
    if not decision_trace.exists():
        raise SystemExit(
            f"decision_trace.jsonl not found in {run_dir}. "
            "Use --record-decision-trace or pick another run with --artifact-dir."
        )

    order_trace = run_dir / "order_trace.json"
    result_path = run_dir / "result.json"
    payload = analyze_run_trace(
        decision_trace_path=decision_trace,
        order_trace_path=order_trace if order_trace.exists() else None,
        result_path=result_path if result_path.exists() else None,
    )

    analysis_output = (
        Path(args.analysis_output).resolve()
        if str(args.analysis_output).strip()
        else run_dir / "analysis_orbit_wall.json"
    )
    analysis_output.parent.mkdir(parents=True, exist_ok=True)
    analysis_output.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"[review-run] analysis: {analysis_output}")

    if args.no_render:
        return

    config = _load_json(run_dir / "config.json") or {}
    result = _load_json(run_dir / "result.json") or {}
    game_over = _load_json(run_dir / "game_over.json") or {}
    state0 = _load_json(run_dir / "state0.json") or {}
    frames = _load_frames(run_dir)
    if not frames:
        raise SystemExit("No replayable frames found. Use --record-decision-trace or --save-states.")

    render_output = (
        Path(args.render_output).resolve()
        if str(args.render_output).strip()
        else run_dir / "ui_replay.html"
    )
    replay_payload = {
        "artifact_dir": str(run_dir),
        "config": config,
        "result": result,
        "game_over": game_over,
        "state0": state0,
        "frames": frames,
    }
    render_output.parent.mkdir(parents=True, exist_ok=True)
    render_output.write_text(_build_html(replay_payload, title=str(args.title)), encoding="utf-8")
    print(f"[review-run] replay:   {render_output}")


if __name__ == "__main__":
    main()

