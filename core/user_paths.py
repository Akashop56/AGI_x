"""Shared paths for project configuration and per-user runtime data."""

from __future__ import annotations

import os
import sys
from pathlib import Path, PureWindowsPath


def get_project_root() -> Path:
    """Return the running checkout/application root, independent of cwd."""
    configured_root = os.environ.get("BRAHMA_PROJECT_ROOT", "").strip()
    if configured_root:
        return Path(configured_root).expanduser().resolve()

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    # This module lives in <project>/core, so its parent is the project root.
    return Path(__file__).resolve().parent.parent


def get_config_dir() -> Path:
    """Return the active project's config directory."""
    return get_project_root() / "config"


def get_api_keys_path() -> Path:
    """Return the canonical API-key file in the active project's config/."""
    return get_config_dir() / "api_keys.json"


def get_user_data_dir() -> Path:
    """Return Brahma's per-user storage directory (memory, logs, and caches)."""
    app_data = os.getenv("LOCALAPPDATA", os.path.expanduser("~"))
    data_dir = Path(app_data).expanduser() / "BrahmaAI"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_workspace_dir(configured: str | os.PathLike[str] | None = None) -> Path:
    """Resolve a configured workspace or choose a portable per-user default.

    Relative workspace values are anchored to the project root instead of the
    process working directory. Stale Windows paths from a settings file are
    ignored on POSIX/Android so they cannot create a bogus ``C:/...`` folder.
    ``BRAHMA_WORKSPACE_DIR`` can be used to override the default.
    """
    candidates = [configured, os.environ.get("BRAHMA_WORKSPACE_DIR")]
    for value in candidates:
        raw = str(value or "").strip()
        if not raw:
            continue
        if os.name != "nt" and PureWindowsPath(raw).drive:
            continue

        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = get_project_root() / path
        return path.resolve()

    return (Path.home() / "BrahmaProjects").resolve()
