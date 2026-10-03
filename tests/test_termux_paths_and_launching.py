from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import user_paths
import actions.browser_control as browser_control_module
import actions.open_app as open_app_module


def test_api_keys_path_resolves_from_active_project_root(monkeypatch, tmp_path):
    project_root = tmp_path / "checkout"
    monkeypatch.setenv("BRAHMA_PROJECT_ROOT", str(project_root))

    assert user_paths.get_project_root() == project_root.resolve()
    assert user_paths.get_config_dir() == project_root / "config"
    assert user_paths.get_api_keys_path() == project_root / "config" / "api_keys.json"


def test_relative_workspace_is_anchored_to_project_root(monkeypatch, tmp_path):
    project_root = tmp_path / "checkout"
    monkeypatch.delenv("BRAHMA_WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("BRAHMA_PROJECT_ROOT", str(project_root))

    assert user_paths.get_workspace_dir("work") == project_root / "work"


@pytest.mark.skipif(os.name == "nt", reason="checks stale Windows paths on POSIX/Android")
def test_stale_windows_workspace_uses_portable_default(monkeypatch):
    monkeypatch.delenv("BRAHMA_WORKSPACE_DIR", raising=False)

    workspace = user_paths.get_workspace_dir(r"C:\Users\ravit\Downloads\Brahma Uploads")

    assert workspace == (Path.home() / "BrahmaProjects").resolve()
    assert not str(workspace).startswith("C:")


def _successful_run(calls):
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return run


def test_open_app_uses_termux_open_for_android_packages(monkeypatch):
    calls = []
    monkeypatch.setattr(open_app_module.pc_compat, "is_android", lambda: True)
    monkeypatch.setattr(open_app_module.subprocess, "run", _successful_run(calls))

    result = open_app_module.open_app({"app_name": "Spotify"})

    assert calls[0][0] == ["termux-open", "com.spotify.music"]
    assert "Opened Spotify" in result


def test_browser_control_opens_urls_with_termux_open(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_control_module.pc_compat, "is_android", lambda: True)
    monkeypatch.setattr(browser_control_module.subprocess, "run", _successful_run(calls))

    result = browser_control_module.browser_control({"action": "navigate", "url": "example.com/docs"})

    assert calls[0][0] == ["termux-open", "https://example.com/docs"]
    assert result == "Opened: https://example.com/docs"


def test_browser_control_searches_on_android(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_control_module.pc_compat, "is_android", lambda: True)
    monkeypatch.setattr(browser_control_module.subprocess, "run", _successful_run(calls))

    browser_control_module.browser_control({
        "action": "search",
        "query": "Termux browser support",
        "engine": "duckduckgo",
    })

    assert calls[0][0] == [
        "termux-open",
        "https://duckduckgo.com/?q=Termux+browser+support",
    ]
