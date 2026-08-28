"""Best-effort OS autostart (ticket 06).

Wires the "开机自启" setting to the platform's login mechanism. Currently
implemented for macOS via a LaunchAgent plist; other platforms are a no-op and
are revisited when packaging lands (ticket 07), where the installed .app / EXE
path is known. The operation is idempotent and reversible (removes the plist on
disable). It is only invoked when the user has explicitly enabled autostart.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.teleflow.app.plist"


def set_autostart(enabled: bool) -> bool:
    """Enable or disable login-time autostart. Returns True if handled."""
    if sys.platform != "darwin":
        return False

    if enabled:
        _LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        _LAUNCH_AGENT.write_text(_plist_content(), encoding="utf-8")
    else:
        _LAUNCH_AGENT.unlink(missing_ok=True)
    return True


def _plist_content() -> str:
    executable = sys.executable
    entry = Path(__file__).resolve().parent.parent / "teleflow" / "app.py"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        "  <key>Label</key><string>com.teleflow.app</string>\n"
        "  <key>ProgramArguments</key><array>\n"
        f"    <string>{executable}</string><string>{entry}</string>\n"
        "  </array>\n"
        "  <key>RunAtLoad</key><true/>\n"
        "  <key>KeepAlive</key><false/>\n"
        "</dict></plist>\n"
    )
