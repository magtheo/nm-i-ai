"""Generate a standalone HTML replay UI for a recorded live artifact run."""
from __future__ import annotations

import argparse
import copy
import html
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate UI replay HTML from live artifact logs")
    parser.add_argument("--artifact-dir", type=str, required=True, help="Path to run_YYYYMMDD_HHMMSS artifact directory")
    parser.add_argument("--output", type=str, default="", help="Output HTML file (default: <artifact-dir>/ui_replay.html)")
    parser.add_argument("--title", type=str, default="NMiAI Expert Live Replay", help="Page title")
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _first_round_log_path(artifact_dir: Path) -> Path | None:
    round_logs_dir = artifact_dir / "round_logs"
    if not round_logs_dir.exists():
        return None
    files = sorted(round_logs_dir.glob("*.jsonl"))
    return files[0] if files else None


def _load_trace_map(path: Path) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    payload = _load_json(path)
    if not payload:
        return out
    trace = payload.get("trace")
    if not isinstance(trace, list):
        return out
    for row in trace:
        if not isinstance(row, dict):
            continue
        try:
            round_idx = int(row.get("round"))
        except Exception:
            continue
        out[round_idx] = row
    return out


def _load_frames(artifact_dir: Path) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    decision_rows = _load_jsonl(artifact_dir / "decision_trace.jsonl")
    if decision_rows:
        for row in decision_rows:
            state = row.get("state")
            if not isinstance(state, dict):
                continue
            frames.append(
                {
                    "round": int(row.get("round", 0)),
                    "score": int(row.get("score", 0)),
                    "max_rounds": int(row.get("max_rounds", state.get("max_rounds", 0))),
                    "active_order_index": int(row.get("active_order_index", state.get("active_order_index", 0))),
                    "decision_ms": float(row.get("decision_ms", 0.0)),
                    "actions": row.get("actions", []),
                    "telemetry": row.get("telemetry", {}),
                    "assignment_snapshot": row.get("assignment_snapshot", {}),
                    "pre_collision_actions": row.get("pre_collision_actions", {}),
                    "wait_reason_by_bot": row.get("wait_reason_by_bot", {}),
                    "state": state,
                }
            )

    if not frames:
        round_log_path = _first_round_log_path(artifact_dir)
        if round_log_path is None:
            return []
        round_rows = _load_jsonl(round_log_path)
        for row in round_rows:
            if row.get("type") == "summary":
                continue
            state = row.get("state")
            if not isinstance(state, dict):
                continue
            frames.append(
                {
                    "round": int(row.get("round", 0)),
                    "score": int(row.get("score", 0)),
                    "max_rounds": int(state.get("max_rounds", 0)),
                    "active_order_index": int(state.get("active_order_index", 0)),
                    "decision_ms": float(row.get("decision_ms", 0.0)),
                    "actions": row.get("actions", []),
                    "telemetry": row.get("telemetry", {}),
                    "assignment_snapshot": {},
                    "pre_collision_actions": {},
                    "wait_reason_by_bot": {},
                    "state": state,
                }
            )
        if not frames:
            frames = _reconstruct_frames_from_actions(artifact_dir=artifact_dir, round_rows=round_rows)

    frames.sort(key=lambda item: int(item.get("round", 0)))

    order_trace_map = _load_trace_map(artifact_dir / "order_trace.json")
    item_trace_map = _load_trace_map(artifact_dir / "item_spawn_trace.json")
    for frame in frames:
        r = int(frame.get("round", 0))
        if r in order_trace_map:
            frame["order_trace"] = order_trace_map[r]
        if r in item_trace_map:
            frame["item_trace"] = item_trace_map[r]

    return frames


def _reconstruct_frames_from_actions(
    *,
    artifact_dir: Path,
    round_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fallback for old logs that have actions/telemetry but no per-round state payloads."""
    state0 = _load_json(artifact_dir / "state0.json")
    if not isinstance(state0, dict):
        return []
    base_bots = state0.get("bots")
    grid = state0.get("grid")
    if not isinstance(base_bots, list) or not isinstance(grid, dict):
        return []

    bot_positions: dict[int, tuple[int, int]] = {}
    bot_inventory: dict[int, list[str]] = {}
    for bot in base_bots:
        if not isinstance(bot, dict):
            continue
        try:
            bid = int(bot.get("id"))
            bx, by = bot.get("position", [0, 0])
            bot_positions[bid] = (int(bx), int(by))
            inv = bot.get("inventory", [])
            if not isinstance(inv, list):
                inv = []
            bot_inventory[bid] = [str(it) for it in inv]
        except Exception:
            continue
    if not bot_positions:
        return []

    width = int(grid.get("width", 0))
    height = int(grid.get("height", 0))
    static_items = copy.deepcopy(state0.get("items", []))
    static_orders = copy.deepcopy(state0.get("orders", []))
    drop_off = state0.get("drop_off", [0, 0])
    max_rounds = int(state0.get("max_rounds", 0))

    delta_by_action: dict[str, tuple[int, int]] = {
        "move_up": (0, -1),
        "move_down": (0, 1),
        "move_left": (-1, 0),
        "move_right": (1, 0),
    }

    frames: list[dict[str, Any]] = []
    for row in sorted(round_rows, key=lambda item: int(item.get("round", 0))):
        if row.get("type") == "summary":
            continue
        actions = row.get("actions", [])
        if not isinstance(actions, list):
            actions = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            try:
                bid = int(action.get("bot"))
            except Exception:
                continue
            current = bot_positions.get(bid)
            if current is None:
                continue
            action_name = str(action.get("action", "")).strip().lower()
            delta = delta_by_action.get(action_name)
            if delta is not None:
                nx = int(current[0] + delta[0])
                ny = int(current[1] + delta[1])
                if width > 0:
                    nx = max(0, min(width - 1, nx))
                if height > 0:
                    ny = max(0, min(height - 1, ny))
                bot_positions[bid] = (nx, ny)

        bots_payload: list[dict[str, Any]] = []
        for bid in sorted(bot_positions):
            pos = bot_positions[bid]
            bots_payload.append(
                {
                    "id": int(bid),
                    "position": [int(pos[0]), int(pos[1])],
                    "inventory": list(bot_inventory.get(bid, [])),
                }
            )

        active_order_index = row.get("active_order_index", state0.get("active_order_index", 0))
        try:
            active_order_index = int(active_order_index)
        except Exception:
            active_order_index = int(state0.get("active_order_index", 0) or 0)

        state_payload = {
            "grid": copy.deepcopy(grid),
            "drop_off": list(drop_off) if isinstance(drop_off, (list, tuple)) else [0, 0],
            "max_rounds": max_rounds,
            "active_order_index": active_order_index,
            "items": copy.deepcopy(static_items),
            "orders": copy.deepcopy(static_orders),
            "bots": bots_payload,
        }
        frames.append(
            {
                "round": int(row.get("round", 0)),
                "score": int(row.get("score", 0)),
                "max_rounds": int(max_rounds),
                "active_order_index": int(active_order_index),
                "decision_ms": float(row.get("decision_ms", 0.0)),
                "actions": actions,
                "telemetry": row.get("telemetry", {}),
                "assignment_snapshot": {},
                "pre_collision_actions": {},
                "wait_reason_by_bot": {},
                "state": state_payload,
            }
        )
    return frames


def _resolve_output_path(artifact_dir: Path, output: str) -> Path:
    if output.strip():
        return Path(output).resolve()
    return (artifact_dir / "ui_replay.html").resolve()


def _build_html(payload: dict[str, Any], title: str) -> str:
    payload_json = json.dumps(payload, ensure_ascii=True)
    safe_title = html.escape(title.strip() or "NMiAI Live Replay")
    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__SAFE_TITLE__</title>
  <style>
    :root {
      --bg: #f2efe8;
      --ink: #172026;
      --muted: #5d6970;
      --panel: #fffdf8;
      --line: #d3cdc0;
      --accent: #0e7c86;
      --accent-2: #d66a36;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 5% 10%, #fff9ec 0, transparent 28%),
        radial-gradient(circle at 95% 90%, #dcecf0 0, transparent 34%),
        var(--bg);
    }
    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 10px;
      padding: 14px;
    }
    .headline {
      background: linear-gradient(120deg, #fff7ea, #ebf4f5);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 8px;
    }
    .headline h1 { margin: 0; font-size: 20px; letter-spacing: 0.3px; }
    .headline .meta { color: var(--muted); font-size: 13px; }
    .layout {
      display: grid;
      grid-template-columns: minmax(460px, 1fr) minmax(340px, 460px);
      gap: 10px;
      min-height: 0;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
    }
    .board-wrap {
      padding: 10px;
      height: 100%;
      display: grid;
      grid-template-rows: auto 1fr;
      gap: 8px;
    }
    .board-head {
      display: flex;
      justify-content: space-between;
      color: var(--muted);
      font-size: 13px;
      gap: 10px;
    }
    #board {
      width: 100%;
      height: 100%;
      min-height: 460px;
      border-radius: 10px;
      border: 1px solid var(--line);
      background: #faf7f1;
      cursor: crosshair;
    }
    .side {
      padding: 10px;
      display: grid;
      gap: 8px;
      align-content: start;
      overflow: auto;
      max-height: calc(100vh - 170px);
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px;
      background: #fff;
    }
    .card h3 { margin: 0 0 6px; font-size: 13px; letter-spacing: 0.2px; color: var(--muted); text-transform: uppercase; }
    .mono { font-family: Consolas, "Courier New", monospace; font-size: 12px; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { border-bottom: 1px solid #ece7dc; padding: 4px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 600; }
    .controls {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 10px;
      display: grid;
      grid-template-columns: auto auto auto 1fr auto;
      gap: 8px;
      align-items: center;
    }
    .btn {
      border: 1px solid var(--line);
      background: #f6f3ec;
      color: var(--ink);
      border-radius: 8px;
      padding: 6px 10px;
      font-size: 12px;
      cursor: pointer;
    }
    .btn:hover { border-color: #b5aea1; }
    input[type=range] { width: 100%; }
    select {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 4px 6px;
    }
    .kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 10px; font-size: 12px; }
    .k { color: var(--muted); }
    .pill {
      display: inline-block;
      border-radius: 999px;
      padding: 1px 7px;
      font-size: 11px;
      border: 1px solid #d7d2c8;
      background: #f7f3ea;
      margin-right: 6px;
      margin-bottom: 4px;
    }
    .swatch {
      width: 11px;
      height: 11px;
      border-radius: 3px;
      display: inline-block;
      margin-right: 6px;
      border: 1px solid #687279;
      vertical-align: middle;
    }
    #shelfLegend {
      max-height: 210px;
      overflow: auto;
      border: 1px solid #ece7dc;
      border-radius: 8px;
      padding: 6px;
      background: #fcfbf7;
    }
    @media (max-width: 1080px) {
      .layout { grid-template-columns: 1fr; }
      .side { max-height: none; }
      #board { min-height: 370px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="headline">
      <h1>__SAFE_TITLE__</h1>
      <div class="meta" id="artifactMeta"></div>
    </header>

    <main class="layout">
      <section class="panel board-wrap">
        <div class="board-head">
          <div id="roundLabel"></div>
          <div id="scoreLabel"></div>
          <div id="cursorLabel">cursor x:- y:- shelf:-</div>
        </div>
        <canvas id="board"></canvas>
      </section>

      <aside class="panel side">
        <div class="card">
          <h3>Run Summary</h3>
          <div id="summaryKv" class="kv"></div>
        </div>
        <div class="card">
          <h3>Bots And Decisions</h3>
          <div id="botsTable"></div>
        </div>
        <div class="card">
          <h3>Orders</h3>
          <div id="ordersBox" class="mono"></div>
        </div>
        <div class="card">
          <h3>Shelf Legend</h3>
          <div id="shelfLegend" class="mono"></div>
        </div>
        <div class="card">
          <h3>Telemetry</h3>
          <div id="telemetryBox" class="mono"></div>
        </div>
        <div class="card">
          <h3>Item Spawn Delta</h3>
          <div id="spawnBox" class="mono"></div>
        </div>
      </aside>
    </main>

    <footer class="controls">
      <button id="prevBtn" class="btn">Prev</button>
      <button id="playBtn" class="btn">Play</button>
      <button id="nextBtn" class="btn">Next</button>
      <input id="roundSlider" type="range" min="0" max="0" value="0" />
      <label class="mono">Speed
        <select id="speedSel">
          <option value="800">1x</option>
          <option value="450">2x</option>
          <option value="220">4x</option>
          <option value="120">8x</option>
        </select>
      </label>
    </footer>
  </div>

  <script>
    const replay = __PAYLOAD_JSON__;
    const frames = Array.isArray(replay.frames) ? replay.frames : [];
    let idx = 0;
    let timer = null;
    let hoverCell = null;
    let boardGeom = null;

    const slider = document.getElementById("roundSlider");
    const playBtn = document.getElementById("playBtn");
    const prevBtn = document.getElementById("prevBtn");
    const nextBtn = document.getElementById("nextBtn");
    const speedSel = document.getElementById("speedSel");

    const canvas = document.getElementById("board");
    const ctx = canvas.getContext("2d");

    const botPalette = [
      "#264653", "#2a9d8f", "#e9c46a", "#f4a261", "#e76f51", "#6d597a",
      "#355070", "#588157", "#bc6c25", "#b56576", "#3d405b", "#277da1"
    ];

    const shelfCatalog = {
      byKey: new Map(),
      order: [],
      nextId: 1,
    };

    function resizeCanvas() {
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(360, Math.floor(rect.width));
      canvas.height = Math.max(320, Math.floor(rect.height));
      render();
    }

    function posKey(x, y) {
      return `${x},${y}`;
    }

    function escapeHtml(text) {
      return String(text || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function hashText(text) {
      const raw = String(text || "");
      let hash = 2166136261;
      for (let i = 0; i < raw.length; i += 1) {
        hash ^= raw.charCodeAt(i);
        hash = Math.imul(hash, 16777619) >>> 0;
      }
      return hash >>> 0;
    }

    function colorForType(kind) {
      const hash = hashText(kind);
      const hue = hash % 360;
      const sat = 56 + (hash % 23);
      const lum = 42 + (hash % 18);
      return `hsl(${hue} ${sat}% ${lum}%)`;
    }

    function typeCode(kind) {
      const clean = String(kind || "?").replace(/[^a-zA-Z0-9]/g, "");
      if (!clean) return "?";
      return clean.slice(0, 2).toUpperCase();
    }

    function botColor(id) {
      return botPalette[Math.abs(Number(id) || 0) % botPalette.length];
    }

    function actionDelta(action) {
      if (action === "move_up") return [0, -1];
      if (action === "move_down") return [0, 1];
      if (action === "move_left") return [-1, 0];
      if (action === "move_right") return [1, 0];
      return [0, 0];
    }

    function ensureShelf(x, y, typeHint) {
      const key = posKey(x, y);
      let shelf = shelfCatalog.byKey.get(key);
      if (!shelf) {
        shelf = {
          id: shelfCatalog.nextId,
          x,
          y,
          base_type: String(typeHint || ""),
        };
        shelfCatalog.nextId += 1;
        shelfCatalog.byKey.set(key, shelf);
        shelfCatalog.order.push(shelf);
      } else if (!shelf.base_type && typeHint) {
        shelf.base_type = String(typeHint);
      }
      return shelf;
    }

    function buildShelfCatalog() {
      const sourceState = (replay.state0 && replay.state0.items) ? replay.state0 : (frames[0]?.state || {});
      const items = Array.isArray(sourceState.items) ? sourceState.items : [];
      for (const item of items) {
        const x = Number(item.position?.[0] || 0);
        const y = Number(item.position?.[1] || 0);
        ensureShelf(x, y, item.type || "");
      }
      shelfCatalog.order.sort((a, b) => (a.y - b.y) || (a.x - b.x));
      for (let i = 0; i < shelfCatalog.order.length; i += 1) {
        shelfCatalog.order[i].id = i + 1;
      }
      shelfCatalog.nextId = shelfCatalog.order.length + 1;
    }
    function coordStep(cell) {
      if (cell >= 28) return 1;
      if (cell >= 20) return 2;
      if (cell >= 14) return 4;
      return 5;
    }

    function drawTypeToken(px, py, cell, itemType) {
      const c = colorForType(itemType);
      const code = typeCode(itemType);
      const tokenW = Math.max(8, Math.floor(cell * 0.72));
      const tokenH = Math.max(8, Math.floor(cell * 0.56));
      const tx = px + Math.floor((cell - tokenW) / 2);
      const ty = py + Math.floor((cell - tokenH) / 2) + Math.floor(cell * 0.08);

      ctx.fillStyle = c;
      ctx.fillRect(tx, ty, tokenW, tokenH);
      ctx.strokeStyle = "#23313a";
      ctx.lineWidth = 1;
      ctx.strokeRect(tx + 0.5, ty + 0.5, tokenW - 1, tokenH - 1);

      ctx.fillStyle = "#ffffff";
      ctx.font = `${Math.max(7, Math.floor(cell * 0.28))}px Consolas`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(code, tx + tokenW / 2, ty + tokenH / 2);
    }

    function drawGridAndAxes(ox, oy, width, height, cell, step) {
      for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) {
          const px = ox + x * cell;
          const py = oy + y * cell;
          ctx.fillStyle = (x + y) % 2 === 0 ? "#fffdf8" : "#f6f2e8";
          ctx.fillRect(px, py, cell, cell);
        }
      }

      for (let x = 0; x <= width; x += 1) {
        ctx.strokeStyle = (x % 5 === 0) ? "#c7bda8" : "#e1d9c9";
        ctx.lineWidth = (x % 5 === 0) ? 1.1 : 0.7;
        const gx = ox + x * cell + 0.5;
        ctx.beginPath();
        ctx.moveTo(gx, oy + 0.5);
        ctx.lineTo(gx, oy + height * cell + 0.5);
        ctx.stroke();
      }
      for (let y = 0; y <= height; y += 1) {
        ctx.strokeStyle = (y % 5 === 0) ? "#c7bda8" : "#e1d9c9";
        ctx.lineWidth = (y % 5 === 0) ? 1.1 : 0.7;
        const gy = oy + y * cell + 0.5;
        ctx.beginPath();
        ctx.moveTo(ox + 0.5, gy);
        ctx.lineTo(ox + width * cell + 0.5, gy);
        ctx.stroke();
      }

      ctx.fillStyle = "#5f665f";
      ctx.font = `${Math.max(8, Math.floor(cell * 0.3))}px Consolas`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (let x = 0; x < width; x += step) {
        const cx = ox + x * cell + cell / 2;
        ctx.fillText(String(x), cx, oy - Math.max(8, Math.floor(cell * 0.4)));
      }
      ctx.textAlign = "right";
      for (let y = 0; y < height; y += step) {
        const cy = oy + y * cell + cell / 2;
        ctx.fillText(String(y), ox - Math.max(8, Math.floor(cell * 0.35)), cy);
      }
    }

    function botOffsets(count, radius) {
      if (count <= 1) return [[0, 0]];
      const out = [];
      for (let i = 0; i < count; i += 1) {
        const ang = (-Math.PI / 2) + ((Math.PI * 2 * i) / count);
        out.push([Math.cos(ang) * radius, Math.sin(ang) * radius]);
      }
      return out;
    }

    function drawFrame(frame) {
      const state = frame.state || {};
      const grid = state.grid || { width: 0, height: 0, walls: [] };
      const width = Number(grid.width || 0);
      const height = Number(grid.height || 0);
      if (width <= 0 || height <= 0) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        boardGeom = null;
        return;
      }

      const padLeft = 42;
      const padTop = 26;
      const padRight = 14;
      const padBottom = 14;
      const cell = Math.max(12, Math.floor(Math.min((canvas.width - padLeft - padRight) / width, (canvas.height - padTop - padBottom) / height)));
      const boardW = cell * width;
      const boardH = cell * height;
      const ox = Math.floor((canvas.width - boardW - padLeft - padRight) / 2) + padLeft;
      const oy = Math.floor((canvas.height - boardH - padTop - padBottom) / 2) + padTop;
      const step = coordStep(cell);
      boardGeom = { ox, oy, cell, width, height, boardW, boardH };

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#f9f4ea";
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      drawGridAndAxes(ox, oy, width, height, cell, step);

      for (const wall of (grid.walls || [])) {
        const x = Number(wall[0]);
        const y = Number(wall[1]);
        const px = ox + x * cell;
        const py = oy + y * cell;
        ctx.fillStyle = "#8f8879";
        ctx.fillRect(px, py, cell, cell);
      }

      if (Array.isArray(state.drop_off) && state.drop_off.length === 2) {
        const dx = Number(state.drop_off[0]);
        const dy = Number(state.drop_off[1]);
        const px = ox + dx * cell;
        const py = oy + dy * cell;
        ctx.fillStyle = "rgba(14,124,134,0.22)";
        ctx.fillRect(px, py, cell, cell);
        ctx.strokeStyle = "#0e7c86";
        ctx.lineWidth = 2;
        ctx.strokeRect(px + 1, py + 1, cell - 2, cell - 2);
        ctx.fillStyle = "#0e7c86";
        ctx.font = `${Math.max(7, Math.floor(cell * 0.28))}px Consolas`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText("DO", px + cell / 2, py + 1);
      }

      const itemByKey = new Map();
      for (const item of (state.items || [])) {
        const x = Number(item.position?.[0] || 0);
        const y = Number(item.position?.[1] || 0);
        ensureShelf(x, y, item.type || "");
        itemByKey.set(posKey(x, y), item);
      }

      for (const shelf of shelfCatalog.order) {
        const px = ox + shelf.x * cell;
        const py = oy + shelf.y * cell;
        const item = itemByKey.get(posKey(shelf.x, shelf.y));

        ctx.fillStyle = "rgba(58, 67, 79, 0.08)";
        ctx.fillRect(px + 1, py + 1, Math.max(2, cell - 2), Math.max(2, cell - 2));

        ctx.fillStyle = "#3b4651";
        ctx.font = `${Math.max(7, Math.floor(cell * 0.24))}px Consolas`;
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        ctx.fillText(String(shelf.id), px + 2, py + 1);

        if (item) {
          drawTypeToken(px, py, cell, String(item.type || shelf.base_type || "unknown"));
        } else {
          ctx.strokeStyle = "rgba(82, 89, 97, 0.35)";
          ctx.lineWidth = 1;
          ctx.strokeRect(px + Math.floor(cell * 0.32), py + Math.floor(cell * 0.38), Math.floor(cell * 0.36), Math.floor(cell * 0.26));
        }
      }
      const actionByBot = new Map();
      for (const action of (frame.actions || [])) {
        actionByBot.set(Number(action.bot), String(action.action || "wait"));
      }

      const botsByCell = new Map();
      for (const bot of (state.bots || [])) {
        const x = Number(bot.position?.[0] || 0);
        const y = Number(bot.position?.[1] || 0);
        const key = posKey(x, y);
        const row = botsByCell.get(key) || [];
        row.push(bot);
        botsByCell.set(key, row);
      }

      for (const [cellKey, bots] of botsByCell.entries()) {
        const [xRaw, yRaw] = cellKey.split(",");
        const x = Number(xRaw);
        const y = Number(yRaw);
        const baseCx = ox + x * cell + cell / 2;
        const baseCy = oy + y * cell + cell / 2;
        const n = bots.length;
        const circleRadius = Math.max(4, cell * (n <= 1 ? 0.25 : 0.16));
        const offsets = botOffsets(n, Math.max(0, cell * 0.28));

        bots.sort((a, b) => Number(a.id || 0) - Number(b.id || 0));
        for (let i = 0; i < bots.length; i += 1) {
          const bot = bots[i];
          const bid = Number(bot.id || 0);
          const [oxBot, oyBot] = offsets[i] || [0, 0];
          const cx = baseCx + oxBot;
          const cy = baseCy + oyBot;

          ctx.fillStyle = botColor(bid);
          ctx.beginPath();
          ctx.arc(cx, cy, circleRadius, 0, Math.PI * 2);
          ctx.fill();
          ctx.strokeStyle = "#1d252c";
          ctx.lineWidth = 1.3;
          ctx.stroke();

          ctx.fillStyle = "#ffffff";
          ctx.font = `${Math.max(7, Math.floor(circleRadius * 0.95))}px Consolas`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(String(bid), cx, cy);

          const action = actionByBot.get(bid) || "wait";
          const [dx, dy] = actionDelta(action);
          if (dx !== 0 || dy !== 0) {
            ctx.strokeStyle = "#1f2933";
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.lineTo(cx + dx * (cell * 0.34), cy + dy * (cell * 0.34));
            ctx.stroke();
          }
        }
      }

      if (hoverCell && hoverCell.x >= 0 && hoverCell.x < width && hoverCell.y >= 0 && hoverCell.y < height) {
        const hx = ox + hoverCell.x * cell;
        const hy = oy + hoverCell.y * cell;
        ctx.strokeStyle = "#d66a36";
        ctx.lineWidth = 2;
        ctx.strokeRect(hx + 1, hy + 1, cell - 2, cell - 2);
      }

      ctx.strokeStyle = "#b8ae9b";
      ctx.lineWidth = 1.2;
      ctx.strokeRect(ox + 0.5, oy + 0.5, boardW, boardH);
    }

    function toRows(obj) {
      const keys = Object.keys(obj || {});
      if (!keys.length) return '<div class="mono">n/a</div>';
      const sorted = keys.sort();
      const rows = sorted.map((key) => `<tr><td>${escapeHtml(key)}</td><td>${escapeHtml(obj[key])}</td></tr>`).join("");
      return `<table><tbody>${rows}</tbody></table>`;
    }

    function renderBots(frame) {
      const state = frame.state || {};
      const actionByBot = new Map();
      for (const action of (frame.actions || [])) {
        actionByBot.set(String(action.bot), action.action || "wait");
      }
      const assignment = frame.assignment_snapshot || {};
      const waitReasons = frame.wait_reason_by_bot || {};

      const rows = (state.bots || []).map((bot) => {
        const botKey = String(bot.id);
        const assign = assignment[botKey] || {};
        const inv = Array.isArray(bot.inventory) ? bot.inventory.join(",") : "";
        const sw = `<span class="swatch" style="background:${botColor(bot.id)}"></span>`;
        return `<tr>
          <td>${sw}${escapeHtml(bot.id)}</td>
          <td>[${escapeHtml(bot.position?.[0] ?? 0)},${escapeHtml(bot.position?.[1] ?? 0)}]</td>
          <td>${escapeHtml(inv || "-")}</td>
          <td>${escapeHtml(actionByBot.get(botKey) || "wait")}</td>
          <td>${escapeHtml(assign.target_type || "-")}</td>
          <td>${escapeHtml(assign.source || "-")}</td>
          <td>${escapeHtml(waitReasons[botKey] || "")}</td>
        </tr>`;
      }).join("");

      return `<table>
        <thead>
          <tr><th>bot</th><th>pos</th><th>inv</th><th>action</th><th>target</th><th>source</th><th>wait</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
    }

    function renderOrders(frame) {
      const state = frame.state || {};
      const orders = Array.isArray(state.orders) ? state.orders : [];
      const active = orders.find((o) => o.status === "active");
      const preview = orders.find((o) => o.status === "preview");

      const block = (label, order) => {
        if (!order) return `<div><span class="pill">${label}</span> none</div>`;
        const req = Array.isArray(order.items_required) ? order.items_required.join(", ") : "";
        const done = Array.isArray(order.items_delivered) ? order.items_delivered.join(", ") : "";
        return `<div>
          <span class="pill">${label}</span>
          id=${escapeHtml(order.id)}<br/>
          req=[${escapeHtml(req)}]<br/>
          delivered=[${escapeHtml(done)}]
        </div>`;
      };

      return `<div class="mono">${block("active", active)}<hr/>${block("preview", preview)}</div>`;
    }

    function renderSpawn(frame) {
      const trace = frame.item_trace || {};
      if (!trace.round && trace.round !== 0) {
        return '<div class="mono">no spawn trace for this round</div>';
      }
      const newIds = (trace.new_item_ids || []).join(", ");
      const removed = (trace.removed_item_ids || []).join(", ");
      const moved = (trace.moved_items || []).map((row) => `${row.id}:${JSON.stringify(row.from)}->${JSON.stringify(row.to)}`).join("<br/>");
      return `<div class="mono">
        new: ${escapeHtml(newIds || "-")}<br/>
        removed: ${escapeHtml(removed || "-")}<br/>
        moved:<br/>${moved || "-"}
      </div>`;
    }
    function renderSummary(frame) {
      const state = frame.state || {};
      const grid = state.grid || {};
      const result = replay.result || {};
      const summary = [
        ["difficulty", replay.config?.difficulty || "unknown"],
        ["map", `${grid.width || 0}x${grid.height || 0}`],
        ["walls", Array.isArray(grid.walls) ? grid.walls.length : 0],
        ["shelves", shelfCatalog.order.length],
        ["bots", Array.isArray(state.bots) ? state.bots.length : 0],
        ["items visible", Array.isArray(state.items) ? state.items.length : 0],
        ["score", frame.score ?? 0],
        ["final score", result.score ?? "-"],
        ["orders done", result.orders_completed ?? "-"],
        ["items delivered", result.items_delivered ?? "-"],
        ["decision ms", (frame.decision_ms || 0).toFixed(2)],
      ];
      return summary.map(([k, v]) => `<div class="k">${escapeHtml(k)}</div><div>${escapeHtml(v)}</div>`).join("");
    }

    function renderShelfLegend(frame) {
      const state = frame.state || {};
      const itemByKey = new Map();
      for (const item of (state.items || [])) {
        const x = Number(item.position?.[0] || 0);
        const y = Number(item.position?.[1] || 0);
        itemByKey.set(posKey(x, y), item);
      }
      const rows = shelfCatalog.order.map((shelf) => {
        const current = itemByKey.get(posKey(shelf.x, shelf.y));
        const kind = String(current?.type || shelf.base_type || "unknown");
        const color = colorForType(kind);
        return `<tr>
          <td>${escapeHtml(shelf.id)}</td>
          <td>[${escapeHtml(shelf.x)},${escapeHtml(shelf.y)}]</td>
          <td><span class="swatch" style="background:${color}"></span>${escapeHtml(typeCode(kind))}</td>
          <td>${escapeHtml(kind)}</td>
        </tr>`;
      }).join("");
      return `<table>
        <thead><tr><th>#</th><th>coord</th><th>mark</th><th>type</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    }

    function updateCursorLabel() {
      if (!hoverCell) {
        document.getElementById("cursorLabel").textContent = "cursor x:- y:- shelf:-";
        return;
      }
      const shelf = shelfCatalog.byKey.get(posKey(hoverCell.x, hoverCell.y));
      const shelfText = shelf ? String(shelf.id) : "-";
      document.getElementById("cursorLabel").textContent = `cursor x:${hoverCell.x} y:${hoverCell.y} shelf:${shelfText}`;
    }

    function render() {
      if (!frames.length) return;
      idx = Math.max(0, Math.min(idx, frames.length - 1));
      slider.value = String(idx);

      const frame = frames[idx];
      drawFrame(frame);

      document.getElementById("artifactMeta").textContent = replay.artifact_dir || "";
      document.getElementById("roundLabel").textContent = `round ${frame.round} / ${Math.max(0, (frame.max_rounds || 1) - 1)}`;
      document.getElementById("scoreLabel").textContent = `score ${frame.score}`;
      document.getElementById("summaryKv").innerHTML = renderSummary(frame);
      document.getElementById("botsTable").innerHTML = renderBots(frame);
      document.getElementById("ordersBox").innerHTML = renderOrders(frame);
      document.getElementById("shelfLegend").innerHTML = renderShelfLegend(frame);
      document.getElementById("telemetryBox").innerHTML = toRows(frame.telemetry || {});
      document.getElementById("spawnBox").innerHTML = renderSpawn(frame);
      updateCursorLabel();
    }

    function stopPlay() {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
      playBtn.textContent = "Play";
    }

    function startPlay() {
      stopPlay();
      const delay = Number(speedSel.value || 800);
      timer = setInterval(() => {
        if (idx >= frames.length - 1) {
          stopPlay();
          return;
        }
        idx += 1;
        render();
      }, delay);
      playBtn.textContent = "Pause";
    }

    function pointerToCell(clientX, clientY) {
      if (!boardGeom) return null;
      const rect = canvas.getBoundingClientRect();
      const px = clientX - rect.left;
      const py = clientY - rect.top;
      const x = Math.floor((px - boardGeom.ox) / boardGeom.cell);
      const y = Math.floor((py - boardGeom.oy) / boardGeom.cell);
      if (x < 0 || y < 0 || x >= boardGeom.width || y >= boardGeom.height) return null;
      return { x, y };
    }

    playBtn.addEventListener("click", () => {
      if (!frames.length) return;
      if (timer === null) startPlay(); else stopPlay();
    });

    prevBtn.addEventListener("click", () => {
      stopPlay();
      idx = Math.max(0, idx - 1);
      render();
    });

    nextBtn.addEventListener("click", () => {
      stopPlay();
      idx = Math.min(frames.length - 1, idx + 1);
      render();
    });

    slider.addEventListener("input", () => {
      stopPlay();
      idx = Number(slider.value || 0);
      render();
    });

    speedSel.addEventListener("change", () => {
      if (timer !== null) startPlay();
    });

    canvas.addEventListener("mousemove", (event) => {
      const cell = pointerToCell(event.clientX, event.clientY);
      if (!cell && !hoverCell) return;
      if (!cell && hoverCell) {
        hoverCell = null;
        render();
        return;
      }
      if (!hoverCell || hoverCell.x !== cell.x || hoverCell.y !== cell.y) {
        hoverCell = cell;
        render();
      }
    });

    canvas.addEventListener("mouseleave", () => {
      if (hoverCell) {
        hoverCell = null;
        render();
      }
    });

    window.addEventListener("resize", resizeCanvas);

    buildShelfCatalog();
    slider.max = String(Math.max(0, frames.length - 1));
    resizeCanvas();
  </script>
</body>
</html>
"""
    return template.replace("__PAYLOAD_JSON__", payload_json).replace("__SAFE_TITLE__", safe_title)


def main() -> None:
    args = parse_args()
    artifact_dir = Path(args.artifact_dir).resolve()
    if not artifact_dir.exists():
        raise SystemExit(f"Artifact directory not found: {artifact_dir}")

    config = _load_json(artifact_dir / "config.json") or {}
    result = _load_json(artifact_dir / "result.json") or {}
    game_over = _load_json(artifact_dir / "game_over.json") or {}
    state0 = _load_json(artifact_dir / "state0.json") or {}
    frames = _load_frames(artifact_dir)

    if not frames:
        raise SystemExit("No replayable frames found. Run live with --save-states or --record-decision-trace first.")

    payload = {
        "artifact_dir": str(artifact_dir),
        "config": config,
        "result": result,
        "game_over": game_over,
        "state0": state0,
        "frames": frames,
    }

    output_path = _resolve_output_path(artifact_dir, args.output)
    output_path.write_text(_build_html(payload, title=args.title), encoding="utf-8")
    print(f"UI replay written: {output_path}")


if __name__ == "__main__":
    main()
