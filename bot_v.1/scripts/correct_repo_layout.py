from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from _lab_paths import (
    AGENTS_ROOT,
    ARCHIVE_ROOT,
    BOT_ROOT,
    EXPERIMENTS_ROOT,
    LAB_ROOT,
    SHARED_ROOT,
    TODAY,
    ensure_dir,
    move_path,
    unique_path,
    write_json,
    write_yaml,
)

LEVELS = ("easy", "medium", "hard", "expert", "nightmare")
SHARED_STATE_DEFAULTS = {
    "active_baselines.yaml": {
        "baselines": {level: {"config": f"{BOT_ROOT.name}/best/{level}/current.json", "status": "pending"} for level in LEVELS}
    },
    "current_priorities.yaml": {
        "priorities": [
            {"id": "infra-lab-001", "title": "validate shared root migration", "owner": "planner_agent", "status": "open"}
        ]
    },
    "open_experiments.yaml": {"open_experiments": []},
    "owners.yaml": {
        "owners": {
            "coding_agent": {"scope": ["bot code", "bot scripts"]},
            "analysis_agent": {"scope": ["replays", "metrics", "artifacts"]},
            "planner_agent": {"scope": ["experiments", "priorities"]},
            "review_agent": {"scope": ["promotion", "rollback", "validation"]},
        }
    },
    "promotion_rules.yaml": {
        "promotion_rules": {
            "canonical_best_path": "<bot_root>/best/<level>/current.json",
            "metadata_path": "<bot_root>/best/<level>/metadata.yaml",
            "require_verdict": True,
            "require_score": True,
            "levels": list(LEVELS),
        }
    },
}


def ensure_lab_layout() -> list[dict[str, str]]:
    created: list[dict[str, str]] = []
    for path in (
        EXPERIMENTS_ROOT / "hypotheses",
        EXPERIMENTS_ROOT / "verdicts",
        SHARED_ROOT / "inbox",
        SHARED_ROOT / "curated",
        SHARED_ROOT / "shared_state",
        AGENTS_ROOT / "coding_agent",
        AGENTS_ROOT / "analysis_agent",
        AGENTS_ROOT / "planner_agent",
        AGENTS_ROOT / "review_agent",
        ARCHIVE_ROOT / "deprecated" / TODAY,
        ARCHIVE_ROOT / "migration_manifests",
    ):
        if not path.exists():
            ensure_dir(path)
            created.append({"type": "directory", "path": str(path.relative_to(LAB_ROOT))})

    for name, payload in SHARED_STATE_DEFAULTS.items():
        path = SHARED_ROOT / "shared_state" / name
        if not path.exists():
            write_yaml(path, payload)
            created.append({"type": "file", "path": str(path.relative_to(LAB_ROOT))})
    return created


def ensure_bot_layout() -> list[dict[str, str]]:
    created: list[dict[str, str]] = []
    for level in LEVELS:
        best_dir = BOT_ROOT / "best" / level
        runs_dir = BOT_ROOT / "runs" / level
        for path in (best_dir, runs_dir):
            if not path.exists():
                ensure_dir(path)
                created.append({"type": "directory", "path": str(path.relative_to(BOT_ROOT))})
        current_json = best_dir / "current.json"
        if not current_json.exists():
            current_json.write_text("{}\n", encoding="utf-8")
            created.append({"type": "file", "path": str(current_json.relative_to(BOT_ROOT))})
        metadata_yaml = best_dir / "metadata.yaml"
        if not metadata_yaml.exists():
            write_yaml(
                metadata_yaml,
                {
                    "level": level,
                    "experiment_id": "pending",
                    "branch": "unknown",
                    "commit": "unknown",
                    "score": None,
                    "date": None,
                },
            )
            created.append({"type": "file", "path": str(metadata_yaml.relative_to(BOT_ROOT))})

    configs_dir = BOT_ROOT / "configs"
    if not configs_dir.exists():
        ensure_dir(configs_dir)
        created.append({"type": "directory", "path": str(configs_dir.relative_to(BOT_ROOT))})
    return created


def move_if_exists(src: Path, dst: Path, moves: list[dict[str, str]]) -> None:
    if not src.exists():
        return
    final_dst = unique_path(dst)
    move_path(src, final_dst)
    moves.append({"source": str(src), "destination": str(final_dst), "action": "moved"})


def migrate_misplaced_shared() -> list[dict[str, str]]:
    moves: list[dict[str, str]] = []
    mapping = {
        BOT_ROOT / "workspace" / "inbox": SHARED_ROOT / "inbox",
        BOT_ROOT / "workspace" / "curated": SHARED_ROOT / "curated",
        BOT_ROOT / "workspace" / "shared_state": SHARED_ROOT / "shared_state",
        BOT_ROOT / "experiments": EXPERIMENTS_ROOT,
        BOT_ROOT / "agents": AGENTS_ROOT,
        BOT_ROOT / "archive": ARCHIVE_ROOT,
    }
    for src, dst in mapping.items():
        move_if_exists(src, dst, moves)

    workspace_root = BOT_ROOT / "workspace"
    if workspace_root.exists() and not any(workspace_root.iterdir()):
        empty_target = ARCHIVE_ROOT / "deprecated" / TODAY / "workspace_empty_shell"
        move_if_exists(workspace_root, empty_target, moves)
    return moves


def write_docs() -> list[dict[str, str]]:
    updated: list[dict[str, str]] = []
    lab_readme = LAB_ROOT / "README.md"
    if not lab_readme.exists():
        lab_readme.write_text(
            "# Grocery Bot Lab\n\n"
            "Global collaboration assets live here. Bot code remains inside the bot folder.\n",
            encoding="utf-8",
        )
        updated.append({"type": "file", "path": str(lab_readme.relative_to(LAB_ROOT))})

    bot_readme = BOT_ROOT / "README.md"
    if not bot_readme.exists():
        bot_readme.write_text(
            "# Grocery Bot\n\n"
            "This folder is bot-local. Shared reports, handoffs, and cross-agent state live one level above under `../shared`, `../experiments`, `../agents`, and `../archive`.\n",
            encoding="utf-8",
        )
        updated.append({"type": "file", "path": str(bot_readme.relative_to(BOT_ROOT))})
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Correct repository layout so shared assets live at lab root.")
    parser.add_argument("--apply", action="store_true", help="Apply reversible folder moves if misplaced shared directories exist.")
    args = parser.parse_args()

    created = ensure_lab_layout() + ensure_bot_layout() + write_docs()
    moves: list[dict[str, str]] = []
    if args.apply:
        moves = migrate_misplaced_shared()

    manifest = {
        "timestamp": datetime.now().isoformat(),
        "lab_root": str(LAB_ROOT),
        "bot_root": str(BOT_ROOT),
        "created": created,
        "moves": moves,
    }
    manifest_path = ARCHIVE_ROOT / "migration_manifests" / f"correct_repo_layout_{TODAY}.json"
    write_json(manifest_path, manifest)
    print(manifest_path)


if __name__ == "__main__":
    main()
