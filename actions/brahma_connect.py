from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from brahma_connect.service import get_service
from core.user_paths import get_project_root


BASE_DIR = get_project_root()
_SERVICE_PROVIDER: Callable[[], Any] | None = None


def set_service_provider(provider: Callable[[], Any] | None) -> None:
    global _SERVICE_PROVIDER
    _SERVICE_PROVIDER = provider


def _service(player=None):
    if _SERVICE_PROVIDER is not None:
        return _SERVICE_PROVIDER()
    if player is not None:
        try:
            service = getattr(player, "brahma_connect_service", None)
            if service is not None:
                return service
        except Exception:
            pass
    return get_service(BASE_DIR)


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _fail(error: str, error_code: str, *, device: str | None = None, action: str | None = None, **extra: Any) -> str:
    payload: dict[str, Any] = {
        "success": False,
        "error": error,
        "error_code": error_code,
    }
    if device is not None:
        payload["device"] = device
    if action is not None:
        payload["action"] = action
    payload.update(extra)
    return _dump(payload)


def _success(**payload: Any) -> str:
    payload.setdefault("success", True)
    return _dump(payload)


def _normalize_target(parameters: dict[str, Any] | None) -> str:
    params = parameters or {}
    return str(
        params.get("device")
        or params.get("target")
        or params.get("device_id")
        or params.get("name")
        or params.get("query")
        or ""
    ).strip()


def _execute_action_name(parameters: dict[str, Any] | None) -> str:
    params = parameters or {}
    return str(params.get("action") or params.get("command") or "").strip()


def _required_params_for_action(action: str) -> list[str]:
    action = (action or "").strip().lower()
    return {
        "launch_app": ["app_name"],
        "close_app": ["app_name"],
        "open_url": ["url"],
        "capture_screen": [],
        "take_photo": [],
        "clipboard_get": [],
        "clipboard_set": ["text"],
        "send_file": ["file_path"],
        "receive_file": ["destination"],
        "media_play": [],
        "media_pause": [],
        "volume_set": ["value"],
        "notification_list": [],
        "get_battery": [],
        "get_device_info": [],
        "mouse_move": ["x", "y"],
        "keyboard_type": ["text"],
        "ui_tap": ["x", "y"],
        "ui_swipe": ["x1", "y1", "x2", "y2"],
        "ui_type": ["text"],
    }.get(action, [])


def _required_capabilities_for_action(action: str) -> list[str]:
    action = (action or "").strip().lower()
    return {
        "launch_app": ["launch_app"],
        "close_app": ["launch_app"],
        "open_url": ["launch_app"],
        "capture_screen": ["screen_capture"],
        "take_photo": ["camera"],
        "clipboard_get": ["clipboard"],
        "clipboard_set": ["clipboard"],
        "send_file": ["files"],
        "receive_file": ["files"],
        "media_play": ["media"],
        "media_pause": ["media"],
        "volume_set": ["media"],
        "notification_list": ["notifications"],
        "get_battery": ["battery"],
        "get_device_info": ["device_info"],
        "mouse_move": ["mouse"],
        "keyboard_type": ["keyboard"],
    }.get(action, [])


def _format_devices(devices: list[dict[str, Any]]) -> str:
    items = []
    for device in devices:
        battery = device.get("battery")
        battery_text = f"{battery}%" if battery is not None else "Unknown"
        items.append({
            "device_id": device.get("device_id", ""),
            "name": device.get("name", "Unknown Device"),
            "platform": device.get("platform", "unknown"),
            "online": bool(device.get("online", False)),
            "battery": battery,
            "battery_text": battery_text,
            "capabilities": list(device.get("capabilities") or []),
        })
    return _dump({"success": True, "count": len(items), "devices": items})


def _single_or_ambiguous(matches: list[dict[str, Any]], *, device_label: str, action: str) -> str:
    if not matches:
        return _fail(
            f"No device matches '{device_label}'.",
            "DEVICE_NOT_FOUND",
            device=device_label,
            action=action,
        )
    if len(matches) > 1:
        return _dump({
            "success": False,
            "error": "Multiple devices matched the request.",
            "error_code": "MULTIPLE_DEVICES",
            "device": device_label,
            "action": action,
            "matches": matches,
        })
    return _dump({"success": True, "device": matches[0]})


def connect_list_devices(parameters: dict[str, Any] | None = None, player=None, speak=None) -> str:
    try:
        service = _service(player)
        return _format_devices(service.list_devices())
    except Exception as exc:
        return _fail(str(exc), "GATEWAY_UNAVAILABLE", action="connect_list_devices")


def connect_get_device(parameters: dict[str, Any] | None = None, player=None, speak=None) -> str:
    params = parameters or {}
    target = _normalize_target(params)
    if not target:
        return _fail("A device name or id is required.", "MISSING_PARAMETERS", action="connect_get_device")
    try:
        service = _service(player)
        direct = service.get_device(target)
        if direct:
            return _dump({"success": True, "device": direct})
        matches = service.resolve_devices(target)
        if not matches:
            return _fail(f"No device matches '{target}'.", "DEVICE_NOT_FOUND", device=target, action="connect_get_device")
        if len(matches) > 1:
            return _dump({
                "success": False,
                "error": "Multiple devices matched the request.",
                "error_code": "MULTIPLE_DEVICES",
                "device": target,
                "action": "connect_get_device",
                "matches": matches,
            })
        return _dump({"success": True, "device": matches[0]})
    except Exception as exc:
        return _fail(str(exc), "GATEWAY_UNAVAILABLE", device=target, action="connect_get_device")


def connect_get_capabilities(parameters: dict[str, Any] | None = None, player=None, speak=None) -> str:
    params = parameters or {}
    target = _normalize_target(params)
    if not target:
        return _fail("A device name or id is required.", "MISSING_PARAMETERS", action="connect_get_capabilities")
    try:
        service = _service(player)
        result = service.get_capabilities(target)
        if not result.get("success", False):
            return _dump(result)
        device = result.get("device") or {}
        return _dump({
            "success": True,
            "device": {
                "device_id": device.get("device_id", ""),
                "name": device.get("name", "Unknown Device"),
                "platform": device.get("platform", "unknown"),
                "online": bool(device.get("online", False)),
            },
            "capabilities": list(result.get("capabilities") or []),
            "permissions": list(result.get("permissions") or []),
        })
    except Exception as exc:
        return _fail(str(exc), "GATEWAY_UNAVAILABLE", device=target, action="connect_get_capabilities")


def connect_pair_device(parameters: dict[str, Any] | None = None, player=None, speak=None) -> str:
    params = parameters or {}
    try:
        service = _service(player)
        pending_id = str(params.get("pending_id") or "").strip()
        if pending_id:
            result = asyncio.run(service.approve_pending_request(pending_id))
            return _dump(result)

        device_name = str(params.get("device_name") or params.get("name") or "Unknown Device").strip()
        platform = str(params.get("platform") or "unknown").strip()
        offer = service.create_pairing_offer(device_name=device_name, platform=platform)
        return _dump({
            "success": True,
            "pairing": offer,
            "message": "Share the pairing code or QR payload with the device agent.",
        })
    except Exception as exc:
        return _fail(str(exc), "GATEWAY_UNAVAILABLE", action="connect_pair_device")


def connect_disconnect_device(parameters: dict[str, Any] | None = None, player=None, speak=None) -> str:
    params = parameters or {}
    target = _normalize_target(params)
    if not target:
        return _fail("A device name or id is required.", "MISSING_PARAMETERS", action="connect_disconnect_device")
    reason = str(params.get("reason") or "Disconnected by Brahma").strip()
    try:
        service = _service(player)
        result = asyncio.run(service.disconnect_device(target, reason=reason))
        return _dump(result)
    except Exception as exc:
        return _fail(str(exc), "GATEWAY_UNAVAILABLE", device=target, action="connect_disconnect_device")


def connect_execute(parameters: dict[str, Any] | None = None, player=None, speak=None) -> str:
    params = dict(parameters or {})
    target = _normalize_target(params)
    action = _execute_action_name(params)
    if not target:
        return _fail("A target device is required.", "MISSING_PARAMETERS", action="connect_execute")
    if not action:
        return _fail("An action name is required.", "MISSING_PARAMETERS", device=target, action="connect_execute")

    command_parameters = dict(params.get("parameters") or {})
    for key, value in params.items():
        if key not in {"device", "target", "device_id", "name", "query", "action", "command", "parameters"}:
            command_parameters.setdefault(key, value)

    missing_params = [key for key in _required_params_for_action(action) if not str(command_parameters.get(key, "")).strip()]
    if action == "launch_app" and not any(
        str(command_parameters.get(key, "")).strip() for key in ("app_name", "package", "package_name")
    ):
        missing_params = ["app_name"]
    if missing_params:
        return _dump({
            "success": False,
            "device": target,
            "action": action,
            "error": f"Missing required parameters: {', '.join(missing_params)}.",
            "error_code": "MISSING_PARAMETERS",
            "missing_parameters": missing_params,
        })

    required_capabilities = _required_capabilities_for_action(action)
    if required_capabilities:
        command_parameters = dict(command_parameters)
        command_parameters["required_capabilities"] = required_capabilities

    try:
        service = _service(player)
        result = service.route_command(target, action, command_parameters)
        if not isinstance(result, dict):
            return _dump({
                "success": True,
                "device": target,
                "action": action,
                "data": result,
            })

        result.setdefault("device", target)
        result.setdefault("action", action)
        if result.get("success", False):
            if "data" not in result and "result" in result:
                result["data"] = result.pop("result")
            return _dump(result)

        error_code = str(result.get("error_code") or "COMMAND_FAILED")
        if error_code == "DEVICE_OFFLINE":
            result["error"] = result.get("error") or f"Your {target} is currently offline."
        return _dump(result)
    except Exception as exc:
        return _fail(str(exc), "GATEWAY_UNAVAILABLE", device=target, action=action)


def execute_android_companion_action(
    action: str,
    parameters: dict[str, Any] | None = None,
    *,
    player=None,
    target: str | None = None,
) -> dict[str, Any]:
    """Run an existing native command on one unambiguous online Android peer.

    This is a selection/response adapter around ``connect_execute``; command
    validation, routing, and execution still use Brahma Connect's existing RPC.
    """
    action = str(action or "").strip().lower()
    command_parameters = dict(parameters or {})
    requested = str(
        target
        or command_parameters.pop("device", None)
        or command_parameters.pop("target", None)
        or command_parameters.pop("device_id", None)
        or ""
    ).strip()

    if action not in {"launch_app", "open_url"}:
        return {
            "success": False,
            "action": action,
            "error": f"Unsupported Android companion action: {action or '(empty)'}.",
            "error_code": "UNSUPPORTED_ACTION",
        }

    try:
        service = _service(player)
        devices = [device for device in service.list_devices() if isinstance(device, dict)]
    except Exception as exc:
        return {
            "success": False,
            "action": action,
            "error": f"Could not inspect connected Android companions: {exc}",
            "error_code": "GATEWAY_UNAVAILABLE",
        }

    android_devices = [
        device
        for device in devices
        if "android" in str(device.get("platform", "")).lower()
    ]

    if requested and requested.casefold() in {"android", "phone", "mobile", "android companion"}:
        matches = android_devices
    elif requested:
        direct_matches = [
            device
            for device in android_devices
            if requested.casefold() in {
                str(device.get("device_id", "")).casefold(),
                str(device.get("name", "")).casefold(),
            }
        ]
        matches = direct_matches or [
            device
            for device in android_devices
            if requested.casefold() in str(device.get("device_id", "")).casefold()
            or requested.casefold() in str(device.get("name", "")).casefold()
        ]
        if not matches:
            non_android_match = any(
                requested.casefold() in str(device.get("device_id", "")).casefold()
                or requested.casefold() in str(device.get("name", "")).casefold()
                for device in devices
            )
            return {
                "success": False,
                "device": requested,
                "action": action,
                "error": (
                    f"'{requested}' is not an Android companion."
                    if non_android_match
                    else f"No Android companion matches '{requested}'."
                ),
                "error_code": "UNSUPPORTED_DEVICE_PLATFORM" if non_android_match else "DEVICE_NOT_FOUND",
            }
    else:
        matches = [device for device in android_devices if bool(device.get("online", False))]
        if not matches:
            if android_devices:
                return {
                    "success": False,
                    "action": action,
                    "error": "The paired Android companion is currently offline.",
                    "error_code": "DEVICE_OFFLINE",
                }
            return {
                "success": False,
                "action": action,
                "error": "No Android companion is paired with Brahma Connect.",
                "error_code": "DEVICE_NOT_FOUND",
            }

    if len(matches) > 1:
        return {
            "success": False,
            "device": requested or "android",
            "action": action,
            "error": "More than one Android companion matches; specify a device id or name.",
            "error_code": "MULTIPLE_DEVICES",
            "matches": [
                {
                    "device_id": device.get("device_id", ""),
                    "name": device.get("name", "Unknown Device"),
                    "online": bool(device.get("online", False)),
                }
                for device in matches
            ],
        }

    device = matches[0]
    device_name = str(device.get("name") or device.get("device_id") or "Android companion")
    if not bool(device.get("online", False)):
        return {
            "success": False,
            "device": device.get("device_id") or device_name,
            "action": action,
            "error": f"Your Android companion, {device_name}, is currently offline.",
            "error_code": "DEVICE_OFFLINE",
        }

    device_target = str(device.get("device_id") or device_name)
    try:
        response = connect_execute(
            {
                "device": device_target,
                "action": action,
                "parameters": command_parameters,
            },
            player=player,
        )
        result = json.loads(response)
        if not isinstance(result, dict):
            raise ValueError("The RPC returned a non-object response.")
        result.setdefault("device", device_target)
        result.setdefault("action", action)
        if "success" not in result:
            result["success"] = False
            result.setdefault("error", "The companion returned no execution status.")
            result.setdefault("error_code", "INVALID_RPC_RESPONSE")
        return result
    except Exception as exc:
        return {
            "success": False,
            "device": device_target,
            "action": action,
            "error": f"Android companion command failed: {exc}",
            "error_code": "GATEWAY_UNAVAILABLE",
        }
