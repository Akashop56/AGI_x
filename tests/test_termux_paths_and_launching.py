from __future__ import annotations

import os
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import user_paths
from core.tool_result import ToolResult
from actions import brahma_connect as brahma_connect_module
import actions.browser_control as browser_control_module
import actions.open_app as open_app_module
import actions.dev_agent as dev_agent_module


class FakeAndroidCompanion:
    def __init__(self, *, online: bool = True, count: int = 1, success: bool = True):
        self.devices = [
            {
                "device_id": f"android_{index:03d}",
                "name": f"Android Phone {index}",
                "platform": "android",
                "online": online,
                "capabilities": ["launch_app", "open_url"],
            }
            for index in range(1, count + 1)
        ]
        self.success = success
        self.commands = []

    def list_devices(self):
        return list(self.devices)

    def route_command(self, target: str, action: str, parameters: dict | None = None):
        self.commands.append((target, action, dict(parameters or {})))
        if self.success:
            return {"success": True, "device": target, "action": action, "data": {"native": True}}
        return {
            "success": False,
            "device": target,
            "action": action,
            "error": "Native Android launch failed.",
            "error_code": "APP_NOT_FOUND",
        }


def _use_service(monkeypatch, service):
    monkeypatch.setattr(brahma_connect_module, "_SERVICE_PROVIDER", lambda: service)


def _successful_run(calls):
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return run


def test_api_keys_path_resolves_from_active_project_root(monkeypatch, tmp_path):
    project_root = tmp_path / "checkout"
    monkeypatch.setenv("BRAHMA_PROJECT_ROOT", str(project_root))

    assert user_paths.get_project_root() == project_root.resolve()
    assert user_paths.get_config_dir() == project_root / "config"
    assert user_paths.get_api_keys_path() == project_root / "config" / "api_keys.json"


def test_api_key_consumers_use_the_canonical_config_file(monkeypatch, tmp_path):
    project_root = tmp_path / "relocated-checkout"
    config_dir = project_root / "config"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "api_keys.json"
    config_file.write_text(json.dumps({"os_system": "linux"}), encoding="utf-8")
    monkeypatch.setenv("BRAHMA_PROJECT_ROOT", str(project_root))

    from actions import computer_control, computer_settings, docx_tools, file_processor, pdf_tools
    from config import get_config

    assert computer_control._load_config() == {"os_system": "linux"}
    assert computer_settings._get_api_key() == ""
    assert docx_tools._get_api_key() == ""
    assert file_processor._get_api_key() == ""
    assert pdf_tools._get_api_key() == ""
    assert get_config() == {"os_system": "linux"}
    assert config_file == user_paths.get_api_keys_path()


def test_config_module_reads_canonical_api_config(monkeypatch, tmp_path):
    project_root = tmp_path / "relocated-checkout"
    config_dir = project_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "api_keys.json").write_text(
        json.dumps({"os_system": "linux", "gemini_api_key": ""}),
        encoding="utf-8",
    )
    monkeypatch.setenv("BRAHMA_PROJECT_ROOT", str(project_root))

    from config import get_config, is_linux

    assert get_config()["os_system"] == "linux"
    assert is_linux()


def test_relative_workspace_is_anchored_to_project_root(monkeypatch, tmp_path):
    project_root = tmp_path / "checkout"
    monkeypatch.delenv("BRAHMA_WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("BRAHMA_PROJECT_ROOT", str(project_root))

    assert user_paths.get_workspace_dir("work") == project_root / "work"


def test_dev_agent_does_not_construct_windows_paths_on_android(monkeypatch, tmp_path):
    monkeypatch.setattr(dev_agent_module.platform, "system", lambda: "Android")
    monkeypatch.setattr(dev_agent_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        dev_agent_module.Path,
        "home",
        classmethod(lambda cls: pytest.fail("Android must not resolve Windows VS Code paths")),
    )
    monkeypatch.setattr(
        dev_agent_module.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("No desktop launcher should run on Android"),
    )

    assert dev_agent_module._open_vscode(tmp_path) is False


def test_default_workspace_is_inside_active_project(monkeypatch, tmp_path):
    project_root = tmp_path / "checkout"
    monkeypatch.delenv("BRAHMA_WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("BRAHMA_PROJECT_ROOT", str(project_root))

    assert user_paths.get_workspace_dir() == project_root / "workspace"


@pytest.mark.skipif(os.name == "nt", reason="checks stale Windows paths on POSIX/Android")
def test_stale_windows_workspace_uses_project_default(monkeypatch, tmp_path):
    project_root = tmp_path / "checkout"
    monkeypatch.delenv("BRAHMA_WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("BRAHMA_PROJECT_ROOT", str(project_root))

    workspace = user_paths.get_workspace_dir(r"C:\Users\ravit\Downloads\Brahma Uploads")

    assert workspace == project_root / "workspace"
    assert not str(workspace).startswith("C:")


def test_open_android_app_uses_companion_rpc_not_termux_open(monkeypatch):
    service = FakeAndroidCompanion()
    calls = []
    _use_service(monkeypatch, service)
    monkeypatch.setattr(open_app_module.pc_compat, "is_android", lambda: True)
    monkeypatch.setattr(open_app_module.subprocess, "run", _successful_run(calls))

    result = open_app_module.open_app({"app_name": "Spotify"})

    assert isinstance(result, ToolResult)
    assert result.success is True
    assert service.commands[0][0:2] == ("android_001", "launch_app")
    assert service.commands[0][2]["app_name"] == "Spotify"
    assert service.commands[0][2]["package"] == "com.spotify.music"
    assert not calls


def test_desktop_open_app_keeps_native_linux_launcher(monkeypatch):
    monkeypatch.setattr(open_app_module, "_is_android", lambda: False)
    monkeypatch.setattr(open_app_module.platform, "system", lambda: "Linux")
    monkeypatch.setitem(open_app_module._OS_LAUNCHERS, "Linux", lambda app_name: app_name == "spotify")

    result = open_app_module.open_app({"app_name": "Spotify"})

    assert result.success is True
    assert str(result) == "Opened Spotify successfully, sir."


def test_desktop_open_app_keeps_native_windows_launcher(monkeypatch):
    monkeypatch.setattr(open_app_module, "_is_android", lambda: False)
    monkeypatch.setattr(open_app_module.platform, "system", lambda: "Windows")
    monkeypatch.setitem(open_app_module._OS_LAUNCHERS, "Windows", lambda app_name: app_name == "Spotify")

    result = open_app_module.open_app({"app_name": "Spotify"})

    assert result.success is True
    assert str(result) == "Opened Spotify successfully, sir."


@pytest.mark.parametrize("package", ["com.example.reader", "com.whatsapp"])
def test_android_package_name_is_not_mistaken_for_a_url(monkeypatch, package):
    service = FakeAndroidCompanion()
    _use_service(monkeypatch, service)
    monkeypatch.setattr(open_app_module.pc_compat, "is_android", lambda: True)
    monkeypatch.setattr(open_app_module.shutil, "which", lambda name: "termux-open")

    result = open_app_module.open_app({"app_name": package})

    assert result.success is True
    assert service.commands[0][1] == "launch_app"
    assert service.commands[0][2]["app_name"] == package
    assert service.commands[0][2]["package"] == package


def test_open_android_app_failure_is_not_reported_as_success(monkeypatch):
    service = FakeAndroidCompanion(success=False)
    _use_service(monkeypatch, service)
    monkeypatch.setattr(open_app_module.pc_compat, "is_android", lambda: True)

    result = open_app_module.open_app({"app_name": "YouTube"})

    assert result.success is False
    assert result.error_code == "APP_NOT_FOUND"
    assert "couldn't open YouTube" in str(result)
    assert service.commands[0][2]["package"] == "com.google.android.youtube"


def test_android_app_launch_requires_one_online_companion(monkeypatch):
    offline_service = FakeAndroidCompanion(online=False)
    _use_service(monkeypatch, offline_service)
    monkeypatch.setattr(open_app_module.pc_compat, "is_android", lambda: True)

    offline_result = open_app_module.open_app({"app_name": "Spotify"})
    assert offline_result.success is False
    assert offline_result.error_code == "DEVICE_OFFLINE"
    assert not offline_service.commands

    multiple_service = FakeAndroidCompanion(count=2)
    _use_service(monkeypatch, multiple_service)
    ambiguous_result = open_app_module.open_app({"app_name": "Spotify"})
    assert ambiguous_result.success is False
    assert ambiguous_result.error_code == "MULTIPLE_DEVICES"
    assert not multiple_service.commands


def test_open_android_url_uses_termux_only_when_available(monkeypatch):
    service = FakeAndroidCompanion()
    calls = []
    _use_service(monkeypatch, service)
    monkeypatch.setattr(open_app_module.pc_compat, "is_android", lambda: True)
    monkeypatch.setattr(open_app_module.shutil, "which", lambda name: "termux-open")
    monkeypatch.setattr(open_app_module.subprocess, "run", _successful_run(calls))

    result = open_app_module.open_app({"app_name": "example.com/docs"})

    assert result.success is True
    assert calls[0][0] == ["termux-open", "https://example.com/docs"]
    assert not service.commands


def test_android_url_falls_back_to_companion_when_termux_opener_missing(monkeypatch):
    service = FakeAndroidCompanion()
    _use_service(monkeypatch, service)
    monkeypatch.setattr(open_app_module.pc_compat, "is_android", lambda: True)
    monkeypatch.setattr(open_app_module.shutil, "which", lambda name: None)

    result = open_app_module.open_app({"app_name": "https://example.com"})

    assert result.success is True
    assert service.commands[0][0:2] == ("android_001", "open_url")
    assert service.commands[0][2]["url"] == "https://example.com"


def test_android_file_launch_fails_explicitly_without_termux_opener(monkeypatch, tmp_path):
    service = FakeAndroidCompanion()
    file_path = tmp_path / "notes.pdf"
    file_path.write_text("test", encoding="utf-8")
    _use_service(monkeypatch, service)
    monkeypatch.setattr(open_app_module.pc_compat, "is_android", lambda: True)
    monkeypatch.setattr(open_app_module.shutil, "which", lambda name: None)

    result = open_app_module.open_app({"app_name": str(file_path)})

    assert result.success is False
    assert result.error_code == "TERMUX_OPENER_UNAVAILABLE"
    assert not service.commands


def test_browser_control_opens_urls_with_termux_when_available(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_control_module.pc_compat, "is_android", lambda: True)
    monkeypatch.setattr(browser_control_module.shutil, "which", lambda name: "termux-open")
    monkeypatch.setattr(browser_control_module.subprocess, "run", _successful_run(calls))

    result = browser_control_module.browser_control({"action": "navigate", "url": "example.com/docs"})

    assert isinstance(result, ToolResult)
    assert result.success is True
    assert calls[0][0] == ["termux-open", "https://example.com/docs"]


def test_browser_control_searches_on_android_through_available_termux_opener(monkeypatch):
    calls = []
    monkeypatch.setattr(browser_control_module.pc_compat, "is_android", lambda: True)
    monkeypatch.setattr(browser_control_module.shutil, "which", lambda name: "termux-open")
    monkeypatch.setattr(browser_control_module.subprocess, "run", _successful_run(calls))

    result = browser_control_module.browser_control({
        "action": "search",
        "query": "Termux browser support",
        "engine": "duckduckgo",
    })

    assert result.success is True
    assert calls[0][0] == [
        "termux-open",
        "https://duckduckgo.com/?q=Termux+browser+support",
    ]


def test_browser_control_uses_companion_if_termux_opener_is_missing(monkeypatch):
    service = FakeAndroidCompanion()
    _use_service(monkeypatch, service)
    monkeypatch.setattr(browser_control_module.pc_compat, "is_android", lambda: True)
    monkeypatch.setattr(browser_control_module.shutil, "which", lambda name: None)

    result = browser_control_module.browser_control({"action": "navigate", "url": "example.com/docs"})

    assert result.success is True
    assert service.commands[0][0:2] == ("android_001", "open_url")
    assert service.commands[0][2]["url"] == "https://example.com/docs"


def test_browser_control_preserves_desktop_mcp_navigation(monkeypatch):
    class FakeMcp:
        def navigate(self, url):
            return f"Opened: {url}"

    from actions import playwright_mcp_client

    monkeypatch.setattr(browser_control_module, "_is_android", lambda: False)
    monkeypatch.setattr(browser_control_module, "async_playwright", object())
    monkeypatch.setattr(playwright_mcp_client, "get_playwright_mcp_client", lambda: FakeMcp())

    result = browser_control_module.browser_control({
        "action": "navigate",
        "url": "https://example.com",
    })

    assert result.success is True
    assert str(result) == "Opened: https://example.com"


def test_android_interactive_browser_action_returns_failure_status(monkeypatch):
    monkeypatch.setattr(browser_control_module.pc_compat, "is_android", lambda: True)

    result = browser_control_module.browser_control({"action": "click", "selector": "#submit"})

    assert result.success is False
    assert result.error_code == "INTERACTIVE_AUTOMATION_UNAVAILABLE"


def test_agent_executor_does_not_complete_a_failed_tool(monkeypatch):
    from agent import executor as executor_module
    from agent.error_handler import ErrorDecision

    plan = {
        "steps": [
            {
                "step": 1,
                "tool": "open_app",
                "description": "Open Spotify",
                "parameters": {"app_name": "Spotify"},
            }
        ]
    }
    monkeypatch.setattr(executor_module, "create_plan", lambda goal: plan)
    monkeypatch.setattr(
        executor_module,
        "_call_tool",
        lambda *args, **kwargs: ToolResult(
            "I couldn't open Spotify.",
            success=False,
            error="No Android companion is online.",
            error_code="DEVICE_OFFLINE",
        ),
    )
    monkeypatch.setattr(
        executor_module,
        "analyze_error",
        lambda *args, **kwargs: {
            "decision": ErrorDecision.ABORT,
            "reason": "The Android companion is offline.",
            "user_message": "",
        },
    )

    result = executor_module.AgentExecutor().execute("open Spotify", speak=None)

    assert result.startswith("Task aborted")
