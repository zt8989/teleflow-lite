#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Send WorkBuddy window keystrokes when a TeleFlow call connects / ends.

Ports the key-sending half of ``sip-lab/phone_ctrl_d.py`` (the FreeSWITCH
resident listener) into a one-shot command that TeleFlow's call-lifecycle
hooks invoke:

  * ``connect``  (off-hook  / CALL_CONNECTED)  -> Ctrl+D        (start recording)
  * ``hangup``   (on-hook   / CALL_ENDED)      -> Ctrl+D + wait 1s + Enter,
                                                  but ONLY when the last IVR
                                                  digit was "0" (i.e. a Vibe
                                                  Coding session actually
                                                  started via key 0)

TeleFlow already fires the off-hook / on-hook events, so this script only has
to perform the keystroke action; the "resident listener" role is handled by the
app's ``off_hook_cmd`` / ``on_hook_cmd`` settings (see ticket
teleflow-sip-hooks/01-02).

Wiring (in ``~/.config/teleflow/config.json``):

    {
      "off_hook_cmd": "python \"<repo>/examples/phone_ctrl_keys.py\" connect",
      "on_hook_cmd":  "python \"<repo>/examples/phone_ctrl_keys.py\" hangup --last-digit {last_digit}"
    }

``{last_digit}`` is substituted by TeleFlow with the last IVR digit pressed in
the call ("" if none); the script only sends the stop keys when it is "0".
You may append ``{call_id}`` in the command and add ``--call-id {call_id}`` to
log which call triggered the keystroke.

Platform notes:
  * Windows : the keystroke is directed at the WorkBuddy window via ctypes
    user32 (Ctrl+D / Enter). If the window can't be found we fall back to a
    global keystroke via pynput.
  * macOS   : we send Cmd+D (not Ctrl+D) to match macOS app shortcuts, using
    Quartz (pyobjc) when available, else AppleScript ``System Events``, else
    pynput. pynput is only imported if the other paths fail.
"""

from __future__ import annotations

import argparse
import sys
import time

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    WB_EXE = "workbuddy.exe"
    WB_TITLE_KEY = "WorkBuddy"
elif sys.platform == "darwin":
    WB_TITLE_KEY = "WorkBuddy"
else:
    print("[phone_ctrl_keys] 仅支持 Windows / macOS", file=sys.stderr)
    raise SystemExit(3)


def _global_keys(mod: str | None, key: str) -> None:
    """Last-resort fallback when the windowed / native key send fails.

    ``mod`` is "cmd" (macOS) or "ctrl" (Windows); ``key`` is "d" / "enter".
    """
    try:
        from pynput.keyboard import Controller, Key

        kb = Controller()
        if mod == "cmd":
            with kb.pressed(Key.cmd):
                kb.press(key)
                kb.release(key)
        elif mod == "ctrl":
            with kb.pressed(Key.ctrl):
                kb.press(key)
                kb.release(key)
        else:
            kb.press(Key.enter)
            kb.release(Key.enter)
        print(f"[{time.strftime('%H:%M:%S')}] >>> [兜底] 全局发送键", flush=True)
    except Exception as exc:  # noqa: BLE001 - never crash the hook
        print(f"[{time.strftime('%H:%M:%S')}] [WARN] 全局兜底失败: {exc}", file=sys.stderr)


if sys.platform == "win32":
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

    def _send_connect() -> None:
        """Send Ctrl+D (start recording) to the WorkBuddy window."""
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

elif sys.platform == "darwin":
    def _darwin_send(mod: str | None, key: str) -> bool:
        """Send a key combo on macOS. Returns True if a native path succeeded.

        ``mod`` is "cmd" (Command) or None; ``key`` is "d" / "enter".
        Tries Quartz (pyobjc) first, then AppleScript ``System Events``.
        """
        vk = {"d": 0x02, "enter": 0x24}.get(key)  # kVK_ANSI_D / kVK_Return
        if vk is None:
            return False
        try:
            import Quartz

            flags = Quartz.kCGEventFlagMaskCommand if mod == "cmd" else 0
            for down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(None, vk, down)
                if flags:
                    Quartz.CGEventSetFlags(ev, flags)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            return True
        except Exception:  # noqa: BLE001 - fall through to AppleScript
            pass
        try:
            import subprocess

            if mod == "cmd":
                script = 'tell application "System Events" to keystroke "d" using command down'
            else:
                script = 'tell application "System Events" to keystroke return'
            subprocess.run(["osascript", "-e", script], check=True)
            return True
        except Exception:  # noqa: BLE001 - fall through to pynput
            return False

    def _darwin_activate() -> None:
        """Bring the WorkBuddy app to the foreground before sending keys."""
        try:
            import subprocess

            subprocess.run(
                ["osascript", "-e", 'tell application "WorkBuddy" to activate'],
                check=True,
            )
            time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001 - best-effort, never crash the hook
            print(f"[{time.strftime('%H:%M:%S')}] [WARN] 激活 WorkBuddy 失败: {exc}", file=sys.stderr)

    def _send_connect() -> None:
        """Send Cmd+D (start recording) on macOS."""
        _darwin_activate()
        if _darwin_send("cmd", "d"):
            print(f"[{time.strftime('%H:%M:%S')}] >>> Cmd+D 已发送（macOS）", flush=True)
        else:
            _global_keys("cmd", "d")

    def _send_enter() -> None:
        """Send Enter (stop recording + confirm) on macOS."""
        _darwin_activate()
        if _darwin_send(None, "enter"):
            print(f"[{time.strftime('%H:%M:%S')}] >>> Enter 已发送（macOS）", flush=True)
        else:
            _global_keys(None, "enter")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="向 WorkBuddy 发送 Ctrl+D(Cmd+D on macOS) / Enter（配合 TeleFlow 通话钩子）"
    )
    parser.add_argument(
        "action",
        choices=["connect", "hangup"],
        help="connect=摘机(start recording); hangup=挂机(stop recording + confirm)",
    )
    parser.add_argument("--call-id", default="", help="可选的 call_id，仅用于日志")
    parser.add_argument(
        "--last-digit",
        default="",
        help="挂机时 TeleFlow 传入的最后一个 IVR 按键；仅当为 0（按过 0 开始过"
        " Vibe Coding）才发送停止键，否则跳过",
    )
    args = parser.parse_args(argv)

    tag = f" call_id={args.call_id}" if args.call_id else ""
    print(f"[{time.strftime('%H:%M:%S')}] 动作 {args.action}{tag}", flush=True)

    if args.action == "connect":
        _send_connect()
    elif args.action == "hangup":
        # Vibe Coding 会话只由按 0 发起（digit hook "connect"）；挂机时只有
        # last_digit == "0" 才需要发停止键，其它情况（按过 1/2 或没按键）
        # 直接跳过，避免误发 Cmd+D+Enter。
        if args.last_digit != "0":
            print(
                f"[{time.strftime('%H:%M:%S')}] 挂机: last_digit={args.last_digit!r} "
                "!= 0，不是 Vibe Coding 会话，跳过停止键",
                flush=True,
            )
            return 0
        _send_connect()
        time.sleep(1)
        _send_enter()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
