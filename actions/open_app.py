# actions/open_app.py
# Brahma AI - Cross-Platform App Launcher

import os
import time
import subprocess
import platform
import shutil
import re
from pathlib import Path
from urllib.parse import urlsplit

from core import pc_compat
from core.tool_result import ToolResult

_ANDROID_APP_PACKAGES = {
    "whatsapp": "com.whatsapp",
    "chrome": "com.android.chrome",
    "google chrome": "com.android.chrome",
    "firefox": "org.mozilla.firefox",
    "spotify": "com.spotify.music",
    "discord": "com.discord",
    "telegram": "org.telegram.messenger",
    "instagram": "com.instagram.android",
    "tiktok": "com.zhiliaoapp.musically",
    "youtube": "com.google.android.youtube",
}


def _is_android() -> bool:
    return pc_compat.is_android() or platform.system().lower() == "android"


def _is_android_package_target(raw: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:com|org|net|io|me|app|dev|edu|gov|in|co|tv|uk)\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*",
            raw,
            flags=re.IGNORECASE,
        )
    )


def _is_url_target(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw or _is_android_package_target(raw):
        return False
    scheme = urlsplit(raw).scheme.lower()
    if scheme in {"http", "https", "mailto", "tel", "intent", "geo", "market", "file"}:
        return True
    if raw.lower().startswith("www."):
        return True
    return bool(re.match(r"^[\w.-]+\.[a-zA-Z]{2,}(?::\d+)?(?:[/?#]|$)", raw))


def _is_file_target(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw or _is_url_target(raw):
        return False
    candidate = Path(raw).expanduser()
    return candidate.exists() or raw.startswith(("/", "./", "../", "~/"))


def _companion_result(action: str, parameters: dict, player=None, target: str | None = None) -> dict:
    try:
        from actions.brahma_connect import execute_android_companion_action

        return execute_android_companion_action(
            action,
            parameters,
            player=player,
            target=target,
        )
    except Exception as exc:
        return {
            "success": False,
            "action": action,
            "error": f"Brahma Connect is unavailable: {exc}",
            "error_code": "GATEWAY_UNAVAILABLE",
        }


def _open_android_url_or_file(target: str, player=None, device: str | None = None) -> ToolResult:
    raw = str(target or "").strip()
    is_url = _is_url_target(raw)
    is_file = _is_file_target(raw)
    if not is_url and not is_file:
        return ToolResult(
            f"'{raw}' is not a URL or an existing file.",
            success=False,
            error_code="INVALID_LAUNCH_TARGET",
        )

    if is_file:
        file_path = Path(raw).expanduser()
        if not file_path.exists():
            return ToolResult(
                f"The file '{raw}' does not exist.",
                success=False,
                error_code="FILE_NOT_FOUND",
            )
        raw = str(file_path.resolve())
    elif not urlsplit(raw).scheme:
        raw = f"https://{raw}"

    opener = shutil.which("termux-open")
    opener_error = None
    if opener:
        try:
            result = subprocess.run(
                ["termux-open", raw],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                return ToolResult(f"Opened {raw}.", data={"opener": "termux-open", "target": raw})
            opener_error = (result.stderr or result.stdout or "termux-open returned a non-zero status.").strip()
        except Exception as exc:
            opener_error = str(exc)

    if is_url:
        rpc_result = _companion_result("open_url", {"url": raw}, player=player, target=device)
        if rpc_result.get("success"):
            return ToolResult(
                f"Opened {raw} on the Android companion.",
                data=rpc_result,
            )
        detail = str(rpc_result.get("error") or opener_error or "No Android URL opener is available.")
        return ToolResult(
            f"Could not open {raw}: {detail}",
            success=False,
            error=detail,
            error_code=str(rpc_result.get("error_code") or "URL_OPEN_FAILED"),
            data=rpc_result,
        )

    detail = opener_error or "termux-open is not installed."
    return ToolResult(
        f"Could not open file {raw}: {detail}",
        success=False,
        error=detail,
        error_code="TERMUX_OPENER_UNAVAILABLE" if not opener else "URL_OPEN_FAILED",
    )


try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_APP_ALIASES = {
    "whatsapp":           {"Windows": "WhatsApp",               "Darwin": "WhatsApp",            "Linux": "whatsapp"},
    "chrome":             {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                "Darwin": "Firefox",             "Linux": "firefox"},
    "spotify":            {"Windows": "Spotify",                "Darwin": "Spotify",             "Linux": "spotify"},
    "vscode":             {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "visual studio code": {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "discord":            {"Windows": "Discord",                "Darwin": "Discord",             "Linux": "discord"},
    "telegram":           {"Windows": "Telegram",               "Darwin": "Telegram",            "Linux": "telegram"},
    "instagram":          {"Windows": "Instagram",              "Darwin": "Instagram",           "Linux": "instagram"},
    "tiktok":             {"Windows": "TikTok",                 "Darwin": "TikTok",              "Linux": "tiktok"},
    "notepad":            {"Windows": "notepad.exe",            "Darwin": "TextEdit",            "Linux": "gedit"},
    "calculator":         {"Windows": "calc.exe",               "Darwin": "Calculator",          "Linux": "gnome-calculator"},
    "terminal":           {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "gnome-terminal"},
    "cmd":                {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "bash"},
    "explorer":           {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "paint":              {"Windows": "mspaint.exe",            "Darwin": "Preview",             "Linux": "gimp"},
    "word":               {"Windows": "winword",                "Darwin": "Microsoft Word",      "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                  "Darwin": "Microsoft Excel",     "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",               "Darwin": "Microsoft PowerPoint","Linux": "libreoffice --impress"},
    "vlc":                {"Windows": "vlc",                    "Darwin": "VLC",                 "Linux": "vlc"},
    "zoom":               {"Windows": "Zoom",                   "Darwin": "zoom.us",             "Linux": "zoom"},
    "slack":              {"Windows": "Slack",                  "Darwin": "Slack",               "Linux": "slack"},
    "steam":              {"Windows": "steam",                  "Darwin": "Steam",               "Linux": "steam"},
    "task manager":       {"Windows": "taskmgr.exe",            "Darwin": "Activity Monitor",    "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",           "Darwin": "System Preferences",  "Linux": "gnome-control-center"},
    "powershell":         {"Windows": "powershell.exe",         "Darwin": "Terminal",            "Linux": "bash"},
    "edge":               {"Windows": "msedge",                 "Darwin": "Microsoft Edge",      "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                  "Darwin": "Brave Browser",       "Linux": "brave-browser"},
    "obsidian":           {"Windows": "Obsidian",               "Darwin": "Obsidian",            "Linux": "obsidian"},
    "notion":             {"Windows": "Notion",                 "Darwin": "Notion",              "Linux": "notion"},
    "blender":            {"Windows": "blender",                "Darwin": "Blender",             "Linux": "blender"},
    "capcut":             {"Windows": "CapCut",                 "Darwin": "CapCut",              "Linux": "capcut"},
    "postman":            {"Windows": "Postman",                "Darwin": "Postman",             "Linux": "postman"},
    "figma":              {"Windows": "Figma",                  "Darwin": "Figma",               "Linux": "figma"},
}


def _normalize(raw: str) -> str:
    system = platform.system()
    key    = raw.lower().strip()
    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(system, raw)
    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(system, raw)
    return raw


def _is_running(app_name: str) -> bool:
    if not _PSUTIL:
        return True
    app_lower = app_name.lower().replace(" ", "").replace(".exe", "")
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                proc_name = proc.info["name"].lower().replace(" ", "").replace(".exe", "")
                if app_lower in proc_name or proc_name in app_lower:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return False


def _launch_windows(app_name: str) -> bool:
    app_lower = app_name.lower().strip()

    # Direct Chrome launching
    if app_lower in ("chrome", "google chrome", "browser", "internet", "web"):
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            shutil.which("chrome"),
        ]
        for cp in chrome_paths:
            if cp and (os.path.exists(cp) if os.path.isabs(cp) else True):
                try:
                    subprocess.Popen([cp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(1.0)
                    return True
                except Exception:
                    pass

    # Direct Spotify launching (app or Chrome web player fallback)
    if app_lower in ("spotify", "spotify music", "spotify web"):
        spotify_paths = [
            os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Spotify\Spotify.exe"),
            r"C:\Program Files\Spotify\Spotify.exe",
            shutil.which("spotify"),
        ]
        for sp in spotify_paths:
            if sp and os.path.exists(sp):
                try:
                    subprocess.Popen([sp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(1.0)
                    return True
                except Exception:
                    pass

        # If Spotify desktop is not installed, open Spotify in Google Chrome
        from actions.spotify_controller import _open_url_in_chrome
        _open_url_in_chrome("https://open.spotify.com")
        time.sleep(1.0)
        return True

    # Try direct binary in PATH or Windows System32
    bin_path = shutil.which(app_name) or shutil.which(f"{app_name}.exe")
    if bin_path:
        try:
            subprocess.Popen([bin_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    # Fallback to Start Menu search
    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(3.0)
        return True
    except Exception as e:
        print(f"[open_app] ⚠️ Windows launch failed: {e}")
        return False

def _launch_macos(app_name: str) -> bool:
    try:
        result = subprocess.run(["open", "-a", app_name], capture_output=True, timeout=8)
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(["open", "-a", f"{app_name}.app"], capture_output=True, timeout=8)
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] ⚠️ macOS Spotlight failed: {e}")
        return False



def _launch_linux(app_name: str) -> bool:
    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-"))
    )
    if binary:
        try:
            subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        result = subprocess.run(["xdg-open", app_name], capture_output=True, timeout=5, check=False)
        if result.returncode == 0:
            return True
    except Exception:
        pass

    try:
        desktop_name = app_name.lower().replace(" ", "-")
        result = subprocess.run(["gtk-launch", desktop_name], capture_output=True, timeout=5, check=False)
        if result.returncode == 0:
            return True
    except Exception:
        pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}



def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> ToolResult:
    params = parameters if isinstance(parameters, dict) else {}
    app_name = str(
        params.get("app_name")
        or params.get("url")
        or params.get("file_path")
        or params.get("path")
        or ""
    ).strip()

    if not app_name:
        return ToolResult(
            "Please specify an application, URL, or file to open, sir.",
            success=False,
            error_code="MISSING_ARGUMENT",
        )

    if player and hasattr(player, "write_log"):
        try:
            player.write_log(f"[open_app] {app_name}")
        except Exception:
            pass

    if _is_android():
        if _is_url_target(app_name) or _is_file_target(app_name):
            return _open_android_url_or_file(
                app_name,
                player=player,
                device=params.get("device") or params.get("target"),
            )

        package = str(
            params.get("package")
            or params.get("package_name")
            or _ANDROID_APP_PACKAGES.get(app_name.casefold())
            or ""
        ).strip()
        if not package and re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", app_name):
            package = app_name

        command_parameters = {"app_name": app_name}
        if package:
            command_parameters["package"] = package
        rpc_result = _companion_result(
            "launch_app",
            command_parameters,
            player=player,
            target=params.get("device") or params.get("target"),
        )
        if rpc_result.get("success"):
            device_name = rpc_result.get("device") or "Android companion"
            return ToolResult(
                f"Opened {app_name} on {device_name}.",
                data=rpc_result,
            )

        detail = str(rpc_result.get("error") or "The companion did not confirm the app launch.")
        return ToolResult(
            f"I couldn't open {app_name} on Android: {detail}",
            success=False,
            error=detail,
            error_code=str(rpc_result.get("error_code") or "APP_LAUNCH_FAILED"),
            data=rpc_result,
        )

    system = platform.system()
    launcher = _OS_LAUNCHERS.get(system)
    normalized = _normalize(app_name)
    if launcher is None:
        return ToolResult(
            f"Unsupported OS: {system}",
            success=False,
            error_code="UNSUPPORTED_OS",
        )

    print(f"[open_app] 🚀 Launching: {app_name} → {normalized} ({system})")
    try:
        success = launcher(normalized)
        if not success and normalized != app_name:
            success = launcher(app_name)

        if success:
            return ToolResult(f"Opened {app_name} successfully, sir.")

        return ToolResult(
            f"I tried to open {app_name}, sir, but couldn't confirm it launched. "
            "It may still be loading or might not be installed.",
            success=False,
            error_code="APP_LAUNCH_FAILED",
        )

    except Exception as exc:
        print(f"[open_app] ❌ {exc}")
        return ToolResult(
            f"Failed to open {app_name}, sir: {exc}",
            success=False,
            error=str(exc),
            error_code="APP_LAUNCH_FAILED",
        )
