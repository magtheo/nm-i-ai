"""API helpers for map lookup and game session acquisition."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import time
from typing import Any

import requests


API_BASE = "https://api.ainm.no"


@dataclass(frozen=True)
class MapInfo:
    id: str
    seed: int
    difficulty: str
    label: str


@dataclass(frozen=True)
class GameSession:
    ws_url: str
    token: str
    map_id: str
    map_seed: int
    difficulty: str
    map_label: str


def _strip_access_token_prefix(value: str) -> str:
    text = value.strip().strip('"').strip("'")
    if text.startswith("access_token="):
        return text.split("=", 1)[1]
    return text


def load_access_token(
    *,
    env_key: str = "AINM_ACCESS_TOKEN",
    env_path: str | os.PathLike[str] = ".env",
) -> str:
    """Load API access token from env var or local .env file."""
    raw = os.getenv(env_key)
    if raw:
        token = _strip_access_token_prefix(raw)
        if token:
            return token

    path = Path(env_path)
    if not path.exists():
        raise RuntimeError(f"Missing token: {env_key} is not set and {path} does not exist")

    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key.strip() == env_key:
            token = _strip_access_token_prefix(value)
            if token:
                return token
            break

    raise RuntimeError(f"Missing token: {env_key} not found in {path}")


def redact_ws_url(ws_url: str) -> str:
    marker = "?token="
    if marker not in ws_url:
        return ws_url
    prefix, token = ws_url.split(marker, 1)
    if len(token) <= 8:
        masked = "***"
    else:
        masked = f"{token[:4]}...{token[-4:]}"
    return f"{prefix}{marker}{masked}"


def list_maps(*, api_base: str = API_BASE, timeout: float = 15.0) -> list[MapInfo]:
    url = f"{api_base}/games/maps"
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    maps: list[MapInfo] = []
    for item in payload:
        maps.append(
            MapInfo(
                id=str(item["id"]),
                seed=int(item.get("seed", 0)),
                difficulty=str(item.get("difficulty", "")).lower(),
                label=str(item.get("label", "")),
            )
        )
    return maps


def map_for_difficulty(
    difficulty: str,
    *,
    api_base: str = API_BASE,
    timeout: float = 15.0,
) -> MapInfo:
    wanted = difficulty.lower().strip()
    for entry in list_maps(api_base=api_base, timeout=timeout):
        if entry.difficulty == wanted:
            return entry
    raise RuntimeError(f"No map found for difficulty={difficulty!r}")


def request_game_session(
    difficulty: str,
    *,
    access_token: str | None = None,
    map_id: str | None = None,
    api_base: str = API_BASE,
    timeout: float = 20.0,
    max_retries: int = 4,
) -> GameSession:
    """Request a session token/ws url for the requested difficulty."""
    token = access_token or load_access_token()
    selected_map = map_for_difficulty(difficulty, api_base=api_base, timeout=timeout)
    chosen_map_id = map_id or selected_map.id

    url = f"{api_base}/games/request"
    response = None
    for attempt in range(max_retries):
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"map_id": chosen_map_id},
            timeout=timeout,
        )
        if response.status_code != 429:
            break
        message = response.text or ""
        match = re.search(r"wait\s+(\d+)s", message, flags=re.IGNORECASE)
        wait_sec = int(match.group(1)) if match else 10
        if attempt >= max_retries - 1:
            break
        time.sleep(max(1, wait_sec + 1))

    if response is None:
        raise RuntimeError("Failed to request game session: empty HTTP response")
    if response.status_code >= 400:
        snippet = response.text.replace("\n", " ")[:200]
        raise RuntimeError(
            f"Failed to request game session (status={response.status_code}): {snippet}"
        )

    payload: dict[str, Any] = response.json()
    map_payload: dict[str, Any] = payload.get("map") or {}

    return GameSession(
        ws_url=str(payload["ws_url"]),
        token=str(payload.get("token", "")),
        map_id=str(map_payload.get("id", chosen_map_id)),
        map_seed=int(map_payload.get("seed", selected_map.seed)),
        difficulty=str(map_payload.get("difficulty", difficulty)).lower(),
        map_label=str(map_payload.get("label", selected_map.label)),
    )
