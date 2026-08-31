#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply the IVR digit-hook + Vibe-Coding key-send config to TeleFlow's local
config (config.json) via ``ConfigStore`` — the sanctioned save path.

Run from the repo root with the venv python. On Windows::

    .venv\\Scripts\\python.exe examples\\setup_ivr_hooks.py

On macOS/Linux::

    .venv/bin/python examples/setup_ivr_hooks.py

What it sets:
  * ``ivr_digit_text`` : menu prompts for 0 / 1 / 2
  * ``ivr_digit_hook`` : 0 -> Ctrl+D (start Vibe Coding)
                         1 -> weather hook (query Ningbo + replay menu)
                         2 -> none (announced as the menu prompt only)
  * ``ivr_exit_digit`` : "0" — pressing 0 exits the one-way menu and bridges the
                         call two-way, so the Vibe Coding session can hear you.
  * ``off_hook_cmd``    : ""  (do NOT send Ctrl+D on off-hook)
  * ``on_hook_cmd``     : send Ctrl+D+Enter (stop Vibe Coding + confirm). Fires on
                         every hang-up; ``{last_digit}`` is passed to the script so
                         it can skip the stop keys unless the Vibe Coding session
                         (key 0) actually started.

``{call_id}`` / ``{last_digit}`` are literal placeholders TeleFlow substitutes
at hook-fire time; they are intentionally kept as raw braces below.
"""

from __future__ import annotations

import sys
from pathlib import Path

from teleflow.config import ConfigStore

REPO = Path(__file__).resolve().parent.parent
if sys.platform == "win32":
    VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
else:
    VENV_PY = REPO / ".venv" / "bin" / "python"
EXAMPLES = REPO / "examples"
CTRL_KEYS = EXAMPLES / "phone_ctrl_keys.py"
WEATHER = EXAMPLES / "weather_hook.py"


def q(path: Path) -> str:
    return f'"{path}"'


# Literal placeholders kept as raw braces — TeleFlow fills them at fire time.
# call_id is quoted in the argument to limit the shell-injection surface
# (the INVITE-supplied call_id is untrusted input).
CALL_ID = "{call_id}"
CALL_ID_ARG = ' --call-id "' + CALL_ID + '"'

off_hook_cmd = ""  # off-hook must NOT send Ctrl+D; the "connect" hook on key 0 does.
# Plain hangup command. It fires on every hang-up; the script receives the last
# pressed digit via {last_digit} and only sends Ctrl+D+Enter when it is "0", i.e.
# the Vibe Coding session (the bridge/exit key, key 0) actually started.
on_hook_cmd = (
    q(VENV_PY)
    + " "
    + q(CTRL_KEYS)
    + " hangup --last-digit {last_digit}"
    + CALL_ID_ARG
)
digit_hook = {
    "0": q(VENV_PY) + " " + q(CTRL_KEYS) + " connect" + CALL_ID_ARG,
    "1": q(VENV_PY) + " " + q(WEATHER) + CALL_ID_ARG,
    "2": "",
}
digit_text = {
    "0": "开始 Vibe Coding",
    "1": "查询宁波天气",
    "2": "查询待办事",
}

store = ConfigStore()
settings = store.load()
settings.ivr_digit_text = digit_text
settings.ivr_digit_hook = digit_hook
settings.ivr_exit_digit = "0"  # pressing 0 bridges the call two-way (Vibe Coding)
settings.off_hook_cmd = off_hook_cmd
settings.on_hook_cmd = on_hook_cmd
store.save(settings)

print("[setup_ivr_hooks] 已应用 IVR hook 配置：")
print("  ivr_exit_digit :", repr(settings.ivr_exit_digit))
print("  off_hook_cmd :", repr(off_hook_cmd))
print("  on_hook_cmd  :", on_hook_cmd)
for key in ("0", "1", "2"):
    print(f"  digit[{key}] text :", repr(digit_text[key]))
    print(f"  digit[{key}] hook :", repr(digit_hook[key]))
