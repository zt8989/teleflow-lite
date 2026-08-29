#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Send WorkBuddy window keystrokes when a TeleFlow call connects / ends.

Ports the key-sending half of ``sip-lab/phone_ctrl_d.py`` (the FreeSWITCH
resident listener) into a one-shot command that TeleFlow's call-lifecycle
hooks invoke:

  * ``connect``  (off-hook  / CALL_CONNECTED)  -> Ctrl+D        (start recording)
  * ``hangup``   (on-hook   / CALL_ENDED)      -> Ctrl+D + wait 1s + Enter
                                                (stop recording + confirm)

TeleFlow already fires the off-hook / on-hook events, so this script only has
to perform the keystroke action; the "resident listener" role is handled by the
app's ``off_hook_cmd`` / ``on_hook_cmd`` settings (see ticket
teleflow-sip-hooks/01-02).

Wiring (in ``~/.config/teleflow/config.json``):

    {
      "off_hook_cmd": "python \"<repo>/examples/phone_ctrl_keys.py\" connect",
      "on_hook_cmd":  "python \"<repo>/examples/phone_ctrl_keys.py\" hangup"
    }

You may append ``{call_id}`` in the command and add ``--call-id {call_id}`` to
log which call triggered the keystroke.

The WorkBuddy window is located by executable name (workbuddy.exe) or title
substring; if it can't be found we fall back to a global keystroke via pynput.
Requires Windows (ctypes user32). pynput is only imported if the window lookup
fails.
"""

from __future__ import annotations

import argparse
import sys
import time

if sys.platform != "win32":
    print("[phone_ctrl_keys] 仅支持 Windows（需要 ctypes user32）", file=sys.stderr)
    raise SystemExit(3)

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WB_EXE = "workbuddy.exe"
WB_TITLE_KEY = "WorkBuddy"


def _process_path(hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.lower()
    finally:
        kernel32.CloseHandle(h)
    return ""


def _window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if not n:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _find_workbuddy_hwnd() -> int | None:
    found: list[int] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def by_exe(hwnd, lp):  # noqa: ANN001 - ctypes callback signature
        if user32.IsWindowVisible(hwnd) and _process_path(hwnd).endswith(WB_EXE):
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(by_exe), 0)
    if found:
        return found[0]

    def by_title(hwnd, lp):  # noqa: ANN001 - ctypes callback signature
        if user32.IsWindowVisible(hwnd) and WB_TITLE_KEY.lower() in _window_title(hwnd).lower():
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(by_title), 0)
    return found[0] if found else None


def _force_foreground(hwnd: int) -> None:
    """Detach the Windows foreground lock and force the target window to front."""
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    my_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread != my_thread:
        user32.AttachThreadInput(my_thread, fg_thread, True)
        attached = True
    user32.ShowWindow(hwnd, 5)  # SW_SHOW
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    if attached:
        user32.AttachThreadInput(my_thread, fg_thread, False)


def _tap_key(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def _send_ctrl_d() -> None:
    hwnd = _find_workbuddy_hwnd()
    if hwnd:
        _force_foreground(hwnd)
        time.sleep(0.15)
        VK_CONTROL, VK_D = 0x11, 0x44
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_D, 0, 0, 0)
        user32.keybd_event(VK_D, 0, 2, 0)  # KEYEVENTF_KEYUP
        user32.keybd_event(VK_CONTROL, 0, 2, 0)
        print(f"[{time.strftime('%H:%M:%S')}] >>> Ctrl+D 已定向发送到 WorkBuddy 窗口", flush=True)
    else:
        _global_keys("ctrl", "d")


def _send_enter() -> None:
    hwnd = _find_workbuddy_hwnd()
    if hwnd:
        _force_foreground(hwnd)
        time.sleep(0.15)
        _tap_key(0x0D)  # VK_RETURN
        print(f"[{time.strftime('%H:%M:%S')}] >>> Enter 已定向发送到 WorkBuddy 窗口", flush=True)
    else:
        _global_keys(None, "enter")


def _global_keys(mod: str | None, key: str) -> None:
    """Last-resort fallback when the WorkBuddy window can't be found."""
    try:
        from pynput.keyboard import Controller, Key

        kb = Controller()
        if mod == "ctrl":
            with kb.pressed(Key.ctrl):
                kb.press(key)
                kb.release(key)
        else:
            kb.press(Key.enter)
            kb.release(Key.enter)
        print(f"[{time.strftime('%H:%M:%S')}] >>> [兜底] 未找到 WorkBuddy 窗口，全局发送键", flush=True)
    except Exception as exc:  # noqa: BLE001 - never crash the hook
        print(f"[{time.strftime('%H:%M:%S')}] [WARN] 全局兜底失败: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="向 WorkBuddy 发送 Ctrl+D / Enter（配合 TeleFlow 通话钩子）"
    )
    parser.add_argument(
        "action",
        choices=["connect", "hangup"],
        help="connect=摘机(start recording); hangup=挂机(stop recording + confirm)",
    )
    parser.add_argument("--call-id", default="", help="可选的 call_id，仅用于日志")
    args = parser.parse_args(argv)

    tag = f" call_id={args.call_id}" if args.call_id else ""
    print(f"[{time.strftime('%H:%M:%S')}] 动作 {args.action}{tag}", flush=True)

    if args.action == "connect":
        _send_ctrl_d()
    elif args.action == "hangup":
        _send_ctrl_d()
        time.sleep(1)
        _send_enter()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
