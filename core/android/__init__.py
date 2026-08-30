"""Canonical Android control subsystem (Rebuild spec §16–19).

This is the single owner of Android device control. The public boundary is
:class:`~core.android.tool.AndroidTool` (``get_android_tool()``); it is reached by
the agent through ``ToolGateway`` -> the ``android_*`` tools. It is NOT a mock:
it drives a real device via ``adb`` (or a signed companion bridge), requires
explicit consent + allowlist, and reports ``android_control_unavailable`` with a
reason when a device/permission is missing.

Status: the backend boundary + permission/audit/consent layers are implemented and
unit/integration tested. Device/emulator **E2E requires a live Android device +
the companion app and is UNTESTED here** — it is never reported as WORKING on mocks.
"""
from .permissions import (AndroidPermissionManager, OP_CLASSES, PermissionDenied,
                          get_permission_manager)
from .secure import (new_pairing_secret, pairing_challenge, pairing_response,
                     verify_pairing, verify, sign)
from .tool import AndroidTool, get_android_tool
from .transport import (AdbAndroidTransport, AndroidTransport, AndroidUnavailable,
                        BridgeAndroidTransport, detect_capability)
from .audit import record, read_log

__all__ = [
    "AndroidTool", "get_android_tool",
    "AndroidTransport", "AdbAndroidTransport", "BridgeAndroidTransport",
    "AndroidUnavailable", "detect_capability",
    "AndroidPermissionManager", "PermissionDenied", "OP_CLASSES", "get_permission_manager",
    "new_pairing_secret", "pairing_challenge", "pairing_response", "verify_pairing",
    "sign", "verify",
    "record", "read_log",
]
