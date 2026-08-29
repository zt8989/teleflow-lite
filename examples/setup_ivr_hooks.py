#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply the IVR digit-hook + Vibe-Coding key-send config to TeleFlow's local
config (config.json) via ``ConfigStore`` — the sanctioned save path.

Run from the repo root with the venv python::

    .venv\\Scripts\\python.exe examples\\setup_ivr_hooks.py

What it sets:
  * ``ivr_digit_text`` : menu prompts for 0 / 1 / 2
  * ``ivr_digit_hook`` : 0 -> Ctrl+D (start Vibe Coding)
                         1 -> weather hook (query Ningbo + replay menu)
                         2 -> none (announced as the menu prompt only)
  * ``off_hook_cmd``    : ""  (do NOT send Ctrl+D on off-hook)
  * ``on_hook_cmd``     : send Ctrl+D+Enter (stop Vibe Coding + confirm). The
                         "only if the last digit was 0" guard lives in the app
                         (attach_hooks), not as a shell test, so it works under
                         Windows cmd.exe.

``{call_id}`` / ``{last_digit}`` are literal placeholders TeleFlow substitutes
at hook-fire time; they are intentionally kept as raw braces below.
"""

from __future__ import annotations

from pathlib import Path

from teleflow.config import ConfigStore

REPO = Path(__file__).resolve().parent.parent
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
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

off_hook_cmd = ""  # off-hook must NOT send Ctrl+D; only pressing 0 does.
# Plain hangup command. The "last digit was 0" guard is enforced in attach_hooks
# (Python), not here as a shell test — that guard failed silently under Windows
# cmd.exe, so Ctrl+D+Enter was never sent on hang-up.
on_hook_cmd = q(VENV_PY) + " " + q(CTRL_KEYS) + " hangup" + CALL_ID_ARG
digit_hook = {
    "0": q(VENV_PY) + " " + q(CTRL_KEYS) + " connect" + CALL_ID_ARG,
    "1": q(VENV_PY) + " " + q(WEATHER) + CALL_ID_ARG,
    "2": "",
}
digit_text = {
    "0": "开始 Vibe Coding",
    "1": "查询宁波天气",
    "2": "查询待办事项：示例待办一、示例待办二，请在终端查看。",
}

store = ConfigStore()
settings = store.load()
settings.ivr_digit_text = digit_text
settings.ivr_digit_hook = digit_hook
settings.off_hook_cmd = off_hook_cmd
settings.on_hook_cmd = on_hook_cmd
settings.ivr_exit_digit = "0"  # digit 0 ends the menu and bridges the call two-way
store.save(settings)

print("[setup_ivr_hooks] 已应用 IVR hook 配置：")
print("  off_hook_cmd :", repr(off_hook_cmd))
print("  on_hook_cmd  :", on_hook_cmd)
for key in ("0", "1", "2"):
    print(f"  digit[{key}] text :", repr(digit_text[key]))
    print(f"  digit[{key}] hook :", repr(digit_hook[key]))
