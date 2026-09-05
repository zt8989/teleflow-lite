#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows-only: 按 0 时按住 Ctrl+Win，挂断时释放并回车（TeleFlow IVR 钩子专用）。

与 ``phone_ctrl_keys.py`` 的 tap 模式（Ctrl+D）不同，本脚本实现“按住/释放”
语义：

  * ``hold``    — 由 ``ivr_digit_hook["0"]`` 触发，按下并保持 Ctrl+Win
                 （只发 DOWN，不抬起），直到挂断。
  * ``release`` — 由 ``on_hook_cmd`` 触发，抬起 Ctrl+Win（只发 UP）→ 等
                 ``--enter-delay`` 秒 → 按 Enter，默认为 1s，可通过
                 ``--enter-delay`` / ``--delay`` 自定义；仅当
                 ``last_digit == "0"`` 时执行，避免非 Vibe Coding 会话误释放。
                 不切换窗口（无 WorkBuddy 定向）。

原理：TeleFlow 的 ``SubprocessHookRunner`` 每次都是 one-shot 进程。本脚本利用
Windows ``keybd_event`` 的全局键盘状态——``hold`` 只注入 DOWN，进程退出后
系统仍认为按键处于按住状态；``release`` 再注入 UP 即可配对释放。中间无需
常驻进程。

状态文件 ``%TEMP%\\teleflow_hold.lock`` 用于去重与兜底：重复 hold 会跳过；
release 即使无记录也会发送 UP 防止卡键。

仅支持 Windows（``sys.platform == "win32"``），在其它平台直接退出 3。

Wiring 示例（写入 ``~/.config/teleflow/config.json`` 或通过脚本）：

    {
      "ivr_digit_hook": {
        "0": "python \"C:\\\\path\\\\to\\\\examples\\\\phone_hold_win.py\" hold --call-id {call_id}"
      },
      "on_hook_cmd": "python \"C:\\\\path\\\\to\\\\examples\\\\phone_hold_win.py\" release --last-digit {last_digit} --call-id {call_id}",
      "off_hook_cmd": ""
    }

    # 自定义回车延迟（默认 1.0s，0 表示不回车）::
    #   "on_hook_cmd": "python \"C:\\\\path\\\\to\\\\examples\\\\phone_hold_win.py\" release --last-digit {last_digit} --enter-delay 1.5"
    #   "on_hook_cmd": "python \"C:\\\\path\\\\to\\\\examples\\\\phone_hold_win.py\" release --last-digit {last_digit} --delay 0"

或直接运行辅助脚本::

    .venv\\Scripts\\python.exe examples\\setup_hold_win.py

手动兜底（卡键时）::

    python examples/phone_hold_win.py release --last-digit 0
    python examples/phone_hold_win.py release --last-digit 0 --enter-delay 0
    del %TEMP%\\teleflow_hold.lock
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

if sys.platform != "win32":
    print("[phone_hold_win] 仅支持 Windows (win32)，当前平台: " + sys.platform, file=sys.stderr)
    raise SystemExit(3)

import ctypes  # noqa: E402  — 仅 Windows 分支

user32 = ctypes.windll.user32

VK_CONTROL = 0x11  # Ctrl
VK_LWIN = 0x5B     # Win (Left Windows)
VK_RETURN = 0x0D   # Enter
KEYEVENTF_KEYUP = 0x0002

HOLD_MARK = Path(tempfile.gettempdir()) / "teleflow_hold.lock"


def _hold() -> None:
    """按下并保持 Ctrl+Win（DOWN）。"""
    if HOLD_MARK.exists():
        print(f"[{time.strftime('%H:%M:%S')}] >>> Ctrl+Win 已处于保持状态，跳过重复 hold", flush=True)
        return
    # 先 Win 后 Ctrl，可减少单独 Win 触发开始菜单的概率
    user32.keybd_event(VK_LWIN, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    try:
        HOLD_MARK.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass
    print(f"[{time.strftime('%H:%M:%S')}] >>> Ctrl+Win 已按下并保持 (hold)", flush=True)


def _release(enter_delay: float = 1.0) -> None:
    """抬起 Ctrl+Win（UP），逆序释放，延迟后按 Enter。

    Args:
        enter_delay: 释放后等待多少秒再按 Enter。<=0 时跳过 Enter。
    """
    was_held = HOLD_MARK.exists()
    # 逆序抬起：先 Ctrl 后 Win
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
    try:
        HOLD_MARK.unlink(missing_ok=True)
    except Exception:
        pass
    if was_held:
        print(f"[{time.strftime('%H:%M:%S')}] >>> Ctrl+Win 已释放 (release)", flush=True)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] >>> Ctrl+Win 释放（无先前 hold 记录，仍发送 UP 兜底）", flush=True)
    # 不切 WorkBuddy 窗口，直接全局发 Enter
    if enter_delay <= 0:
        print(f"[{time.strftime('%H:%M:%S')}] >>> 跳过 Enter (enter_delay={enter_delay})", flush=True)
        return
    time.sleep(enter_delay)
    user32.keybd_event(VK_RETURN, 0, 0, 0)
    user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
    print(f"[{time.strftime('%H:%M:%S')}] >>> Enter 已发送 (delay={enter_delay}s)", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Windows-only: 按 0 hold Ctrl+Win，挂断 release 并回车（不切 WorkBuddy 窗口）"
    )
    parser.add_argument(
        "action",
        choices=["hold", "release"],
        help="hold=按 0 后按住 Ctrl+Win；release=挂断时释放并按 Enter（仅 last_digit==0）",
    )
    parser.add_argument("--call-id", default="", help="可选 call_id，仅用于日志")
    parser.add_argument(
        "--last-digit",
        default="",
        help="release 时 TeleFlow 传入的最后 IVR 按键，仅 0 才释放",
    )
    parser.add_argument(
        "--enter-delay",
        "--delay",
        dest="enter_delay",
        type=float,
        default=1.0,
        help="release 后按 Enter 前的等待秒数，默认 1.0；设为 0 跳过 Enter",
    )
    args = parser.parse_args(argv)

    tag = f" call_id={args.call_id}" if args.call_id else ""
    print(f"[{time.strftime('%H:%M:%S')}] 动作 {args.action}{tag}", flush=True)

    if args.action == "hold":
        _hold()
    elif args.action == "release":
        if args.last_digit != "0":
            print(
                f"[{time.strftime('%H:%M:%S')}] 释放: last_digit={args.last_digit!r} != 0，跳过",
                flush=True,
            )
            return 0
        _release(enter_delay=args.enter_delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
