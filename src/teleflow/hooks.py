"""External command hooks for SIP call-lifecycle events (ticket 01).

A hook is a user-configured local shell command executed when a call-lifecycle
event fires — here, "摘机" (off-hook): the moment the current SIP auto-answers an
incoming call. The command is rendered with a small context (currently
``{call_id}``) and launched fire-and-forget in a background thread so it never
stalls the SIP signalling or UI thread; a failed command is logged and swallowed
so a misconfigured hook can never disturb an active call.

The runner is injected behind the ``HookRunner`` protocol, so the SIP core and
the app stay unit-testable with a fake — no subprocess is spawned in tests.
"""

from __future__ import annotations

import subprocess
import threading
from typing import Callable, Protocol

from teleflow.config import ConfigStore
from teleflow.sip import EVENT_CALL_CONNECTED, EVENT_CALL_ENDED, EVENT_IVR_DIGIT, SipCoreService


class HookRunner(Protocol):
    """Executes a rendered hook command. Implementations must be non-blocking
    and must not raise out of ``run``."""

    def run(self, command: str, context: dict[str, str]) -> None: ...


class SubprocessHookRunner:
    """Runs hook commands as local shell processes.

    The command is rendered from ``context`` (``{key}`` placeholders) and
    launched in a daemon thread so ``run`` returns immediately. Output is
    discarded and the exit code is ignored. A failed command is logged and never
    propagated — hook execution is a side-effect, never on the call's critical
    path. The command is trusted local configuration, so ``shell=True`` is used
    to let users write scripts with arguments and pipes.
    """

    def __init__(
        self, store: ConfigStore, log: Callable[[str], None] | None = None
    ) -> None:
        self._store = store
        self._log = log

    def run(self, command: str, context: dict[str, str]) -> None:
        if not command or not command.strip():
            return
        rendered = self._render(command, context)
        self._log_line(f"[HOOK] 执行命令: {rendered}")
        threading.Thread(target=self._execute, args=(rendered,), daemon=True).start()

    def _execute(self, command: str) -> None:
        try:
            subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                # Output is discarded, but a hook command may print bytes that
                # are invalid in UTF-8 (e.g. a tool emitting the system code
                # page). Without errors="replace", subprocess's reader thread
                # raises UnicodeDecodeError and crashes (the exception lands in
                # a daemon thread, outside this try/except). Replace keeps the
                # decode lossy instead of fatal.
                errors="replace",
                check=False,
            )
        except Exception as exc:  # noqa: BLE001 - a hook must never break the call
            self._log_line(f"[HOOK][ERROR] 命令执行失败: {exc}")

    @staticmethod
    def _render(command: str, context: dict[str, str]) -> str:
        out = command
        for key, value in context.items():
            out = out.replace("{" + key + "}", value)
        return out

    def _log_line(self, line: str) -> None:
        if self._log is not None:
            self._log(line)


def attach_hooks(service: SipCoreService, runner: HookRunner, store: ConfigStore) -> None:
    """Subscribe the configured off-hook and on-hook hooks to the SIP core.

    Both read their command from the live config at fire time, so a change made
    in the settings modal takes effect on the next call without a restart:
      - off-hook (摘机) fires on ``CALL_CONNECTED`` — the auto-answer moment.
      - on-hook  (挂机) fires on ``CALL_ENDED`` — the call ends, e.g. the
        landline sends BYE (or a CANCEL before answer).
    """

    def _off_hook(call_id: str) -> None:
        runner.run(store.load().off_hook_cmd, {"call_id": call_id})

    def _on_hook(call_id: str, last_digit: str = "") -> None:
        settings = store.load()
        # Only fire the on-hook command when the last IVR digit was the configured
        # "exit IVR" key (ivr_exit_digit, default "0"): that key began the
        # recording via its per-digit hook, so hanging up must stop + confirm it.
        # The guard lives here in Python rather than as a ``[ "{last_digit}" = "0" ]``
        # test embedded in the command, because on Windows ``shell=True`` runs under
        # cmd.exe where ``[`` is not a command and the ``&&`` would short-circuit it.
        if not settings.ivr_exit_digit or last_digit != settings.ivr_exit_digit:
            return
        runner.run(settings.on_hook_cmd, {"call_id": call_id, "last_digit": last_digit})

    def _on_digit(call_id: str, digit: str) -> None:
        # Per-digit IVR command; empty => no command configured for this key, so
        # skip entirely (a key without a hook should not fire a blank command).
        command = store.load().ivr_digit_hook.get(digit, "")
        if not command or not command.strip():
            return
        runner.run(command, {"call_id": call_id, "digit": digit})

    service.on(EVENT_CALL_CONNECTED, _off_hook)
    service.on(EVENT_CALL_ENDED, _on_hook)
    service.on(EVENT_IVR_DIGIT, _on_digit)
