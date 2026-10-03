from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.user_paths import get_user_data_dir, get_workspace_dir
from actions.brahma_dev_agent import run_dev_agent
from actions.dev_agent import dev_agent


SETTINGS_PATH = get_user_data_dir() / "config" / "app_settings.json"


def _load_settings() -> dict[str, Any]:
    try:
        if SETTINGS_PATH.exists():
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _selected_workspace(parameters: dict[str, Any]) -> str:
    settings = _load_settings()
    configured = (
        parameters.get("workspace_path")
        or parameters.get("project_dir")
        or parameters.get("output_dir")
        or settings.get("developer_mode_workspace")
    )
    return str(get_workspace_dir(configured))


def run_developer_mode_request(parameters: dict[str, Any], speak=None) -> str:
    params = dict(parameters or {})
    description = str(params.get("description") or params.get("brief") or "").strip()
    workspace = _selected_workspace(params)
    Path(workspace).mkdir(parents=True, exist_ok=True)

    params["workspace_path"] = workspace
    params["output_dir"] = workspace

    # Run native Brahma Dev Agent powered by Claude Code architecture & tools
    try:
        return run_dev_agent(params, speak=speak)
    except Exception as exc:
        print(f"[ClaudeBridge] BrahmaDevAgent encountered error: {exc}, falling back to legacy dev agent")
        params.setdefault("language", params.get("language") or "python")
        params.setdefault("project_name", params.get("project_name") or "brahma_project")
        return dev_agent(params, player=None, speak=speak)
