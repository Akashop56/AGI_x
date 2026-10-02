"""core/pc_compat.py — Android / Termux portability helpers.

The brain (Gemini Live session, memory, dynamic skills) is platform agnostic.
The *senses* are not: keyboard automation, clipboard access, screen capture and
the MCI audio player only exist on a desktop OS.  Brahma now runs headless on
Android (Termux) with the companion app acting as the body, so every desktop
only capability has to answer three questions identically:

1. Can this module even be imported on this device?       -> ``optional_import``
2. Is the capability available right now?                 -> ``*_available`` helpers
3. What should the brain be told when it is not?          -> ``unavailable`` message

Nothing in here touches the LLM, memory or skill execution paths.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


# --------------------------------------------------------------------------
# Platform detection
# --------------------------------------------------------------------------

def is_termux() -> bool:
    """True when running inside a Termux (Android) userland."""
    if os.environ.get("TERMUX_VERSION"):
        return True
    prefix = os.environ.get("PREFIX", "")
    if "com.termux" in prefix:
        return True
    try:
        return "com.termux" in str(Path(sys.executable).resolve())
    except Exception:
        return False


def is_android() -> bool:
    return is_termux() or "ANDROID_ROOT" in os.environ


def platform_tag() -> str:
    """Short, log friendly platform name used in status events."""
    if is_termux():
        return "termux"
    if is_android():
        return "android"
    return sys.platform


def is_desktop() -> bool:
    """True when a desktop session (Windows/macOS/X11) is available."""
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def has_display() -> bool:
    return is_desktop() and not is_android()


def safe_windll() -> Any:
    """Return ``ctypes.windll`` on Windows, otherwise a no-op stand-in.

    Modules that only need the Windows API on Windows can call this once and
    then keep their existing call sites.  Any attribute access on the returned
    object yields a callable that returns 0, mirroring a failed API call.
    """

    class _NullCall:
        def __call__(self, *args: Any, **kwargs: Any) -> int:
            return 0

        def __getattr__(self, name: str) -> "_NullCall":  # pragma: no cover
            return _NullCall()

    class _NullWindll:
        def __getattr__(self, name: str) -> _NullCall:
            return _NullCall()

    try:
        import ctypes

        return getattr(ctypes, "windll", _NullWindll())
    except Exception:  # pragma: no cover
        return _NullWindll()


def optional_import(module: str, package: Optional[str] = None) -> Any:
    """Import ``module`` or return ``None`` instead of raising ImportError."""
    try:
        return importlib.import_module(module, package)
    except Exception:
        return None


def unavailable(feature: str, *, detail: str = "") -> str:
    """Consistent, non-crashing answer for a desktop-only capability."""
    target = "the companion app" if is_android() else "this device"
    message = f"{feature} is not available on {target}."
    if detail:
        message += f" {detail}"
    return message


# --------------------------------------------------------------------------
# Termux:API bridge
# --------------------------------------------------------------------------

def termux_api(command: str) -> Optional[str]:
    """Run a Termux:API command, returning stdout or ``None`` if unavailable."""
    if not is_android():
        return None
    exe = shutil.which(f"termux-{command}") or shutil.which(command)
    if exe is None:
        return None
    try:
        result = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except Exception:
        return None


# --------------------------------------------------------------------------
# Clipboard
# --------------------------------------------------------------------------

def clipboard_get() -> str:
    """Read the clipboard on any platform. Never raises, may return ''."""
    text = termux_api("clipboard-get")
    if text is not None:
        return text
    try:
        import pyperclip  # type: ignore

        return pyperclip.paste() or ""
    except Exception:
        return ""


def clipboard_set(text: str) -> bool:
    """Write the clipboard on any platform. Never raises."""
    if is_android():
        exe = shutil.which("termux-clipboard-set")
        if exe is None:
            return False
        try:
            subprocess.run([exe, str(text)], check=False, timeout=5)
            return True
        except Exception:
            return False
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(str(text))
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Misc desktop-only conveniences
# --------------------------------------------------------------------------

def notify(title: str, body: str = "") -> bool:
    """Best-effort user notification (Termux:API, macOS, Linux)."""
    if is_android():
        exe = shutil.which("termux-notification")
        if exe is None:
            return False
        try:
            subprocess.run(
                [exe, "--title", str(title), "--content", str(body)],
                check=False,
                timeout=5,
            )
            return True
        except Exception:
            return False
    if sys.platform == "darwin":
        exe = shutil.which("osascript")
        if exe is None:
            return False
        script = f'display notification "{body}" with title "{title}"'
        try:
            subprocess.run([exe, "-e", script], check=False, timeout=5)
            return True
        except Exception:
            return False
    return False


def open_path(path: str | Path) -> bool:
    """Open a file or URL with the platform's default handler."""
    target = str(path)
    if is_android():
        exe = shutil.which("termux-open") or shutil.which("termux-open-url")
        if exe is None:
            return False
        try:
            subprocess.Popen([exe, target])
            return True
        except Exception:
            return False
    try:
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", target])
            return True
        exe = shutil.which("xdg-open")
        if exe:
            subprocess.Popen([exe, target])
            return True
    except Exception:
        pass
    return False


def audio_hint() -> str:
    """Human readable hint used when local audio hardware is missing."""
    if is_android():
        return (
            "Local audio hardware is unavailable in Termux. The companion app "
            "streams microphone audio and plays Brahma's voice over the Brahma "
            "Connect gateway."
        )
    return "Install the optional audio extras (sounddevice + PortAudio) to enable local voice."
