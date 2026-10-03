import json

from core.user_paths import get_api_keys_path


def get_config() -> dict:
    with get_api_keys_path().open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux'"""
    return get_config().get("os_system", "windows").lower()


def is_windows() -> bool:
    return get_os() == "windows"


def is_mac() -> bool:
    return get_os() == "mac"


def is_linux() -> bool:
    return get_os() == "linux"
