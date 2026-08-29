"""Logging subsystem (ticket 05).

A small, dependency-free event logger: it writes formatted lines to a local file
and forwards the same line to an optional sink (the UI's live log view). Level
filtering is applied at log time. The ``attach`` helper wires the SIP service and
Audio Device Manager events into one logger so SIP signaling, media state, and
device binding all land in the same place — file + UI — as the spec requires.

Unified log API: every app log line — attach()'s SIP/CALL/AUDIO events AND the
ad-hoc "[CATEGORY] message" lines produced by the service, IVR, hooks, and TTS
(cache hits, reports…, via ``log_line``) — flows through the same EventLogger so
the UI panel and the log file always record the same content. A terminal (console)
stream is optional (``console=True``), for runs that want app lines on stdout too;
the terminal may still show extra lines (pjsua2's own C logs), which is fine.

The logger deliberately has no Qt dependency so it is unit-testable headless.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

from teleflow.audio import (
    EVENT_AUDIO_DEVICES_CHANGED, EVENT_DEVICE_SELECTED, EVENT_DEVICES_ENUMERATED,
    EVENT_PRESET_APPLIED, AudioDeviceManager,
)
from teleflow.sip import (
    EVENT_CALL_CONNECTED,
    EVENT_CALL_ENDED,
    EVENT_CALL_INCOMING,
    EVENT_MEDIA_ERROR,
    EVENT_SIP_REGISTERED,
    EVENT_SIP_REGISTER_FAILED,
    EVENT_SIP_STARTED,
    EVENT_SIP_STOPPED,
    EVENT_SIP_UNREGISTERED,
    SipCoreService,
)

DEFAULT_LOG_PATH = Path.home() / ".config" / "teleflow" / "teleflow.log"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


_SEVERITY = {
    LogLevel.DEBUG: 10,
    LogLevel.INFO: 20,
    LogLevel.WARNING: 30,
    LogLevel.ERROR: 40,
}

MessageSink = Callable[[str], None]


class EventLogger:
    """Appends timestamped, level-filtered lines to a file and an optional sink.

    Logging must never crash the app, so file I/O failures are swallowed.
    """

    def __init__(
        self,
        path: Path | None = None,
        level: LogLevel = LogLevel.INFO,
        sink: MessageSink | None = None,
        max_bytes: int = 1 << 20,
        backup_count: int = 5,
        console: bool = False,
    ) -> None:
        self._path = path or DEFAULT_LOG_PATH
        self._level = level
        self._sink = sink
        self._console = console
        # Bounded file growth for 7x24 operation: rotate by size, keep N backups.
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def set_level(self, level: LogLevel) -> None:
        self._level = level

    def log(self, level: LogLevel, category: str, message: str) -> None:
        if _SEVERITY[level] < _SEVERITY[self._level]:
            return
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {level.value} [{category}] {message}"
        self._ensure_dir()
        self._maybe_rotate(len(line) + 1)
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        if self._console:
            # Optional terminal mirror; the same line, so the console shows the
            # app's log content (the terminal may additionally show pjsua2's own
            # C-level logs — that extra noise is expected, not a drift).
            try:
                print(line, flush=True)
            except OSError:
                pass
        if self._sink is not None:
            self._sink(line)

    def log_line(self, line: str) -> None:
        """Route a pre-formatted ``[CATEGORY] message`` line (the service /
        hook / TTS style) through the same file(+console)+sink path.

        This is the funnel for ad-hoc app logs (``[IVR] …``, ``[HOOK] …``,
        ``[REPORT] …``, ``[TTS] …``): what the UI panel shows is exactly what
        the log file records. Level markers embedded in the line (``[ERROR]``,
        ``[WARN...]``) are honored; everything else is INFO.
        """
        upper = line.upper()
        if "[ERROR]" in upper:
            level = LogLevel.ERROR
        elif "[WARN" in upper:
            level = LogLevel.WARNING
        else:
            level = LogLevel.INFO
        category = "APP"
        message = line
        start = line.find("[")
        end = line.find("]", start + 1) if start != -1 else -1
        if start != -1 and end != -1:
            category = line[start + 1 : end].strip() or category
            message = line[end + 1 :].lstrip()
            # Drop a nested "[ERROR]"/"[WARN]" marker left in the message, so
            # "[HOOK][ERROR] boom" renders as "ERROR [HOOK] boom", not twice.
            for tag in ("[ERROR]", "[WARNING]", "[WARN]"):
                if message.upper().startswith(tag):
                    message = message[len(tag) :].lstrip()
                    break
        self.log(level, category, message)

    def _maybe_rotate(self, extra: int) -> None:
        if self._max_bytes <= 0 or self._backup_count <= 0:
            return
        try:
            size = self._path.stat().st_size if self._path.exists() else 0
        except OSError:
            return
        if size + extra < self._max_bytes:
            return
        self._rotate()

    def _rotate(self) -> None:
        # teleflow.log -> teleflow.log.1; .1 -> .2 ... up to backup_count; drop the rest.
        try:
            for i in range(self._backup_count, 1, -1):
                src = self._path.parent / f"{self._path.name}.{i - 1}"
                dst = self._path.parent / f"{self._path.name}.{i}"
                if src.exists():
                    src.replace(dst)
            first = self._path.parent / f"{self._path.name}.1"
            if self._path.exists():
                self._path.replace(first)
        except OSError:
            pass

    def debug(self, category: str, message: str) -> None:
        self.log(LogLevel.DEBUG, category, message)

    def info(self, category: str, message: str) -> None:
        self.log(LogLevel.INFO, category, message)

    def warning(self, category: str, message: str) -> None:
        self.log(LogLevel.WARNING, category, message)

    def error(self, category: str, message: str) -> None:
        self.log(LogLevel.ERROR, category, message)


def attach(logger: EventLogger, service: SipCoreService, manager: AudioDeviceManager) -> None:
    """Subscribe the logger to SIP and audio device events."""
    service.on(EVENT_SIP_STARTED, lambda: logger.info("SIP", "service started"))
    service.on(EVENT_SIP_STOPPED, lambda: logger.info("SIP", "service stopped"))
    service.on(
        EVENT_SIP_REGISTERED,
        lambda contact: logger.info("SIP", f"SIP registered: {contact}"),
    )
    service.on(
        EVENT_SIP_UNREGISTERED,
        lambda: logger.info("SIP", "SIP unregistered"),
    )
    service.on(
        EVENT_SIP_REGISTER_FAILED,
        lambda code, reason: logger.info("SIP", f"SIP registration failed: {code} {reason}"),
    )
    service.on(EVENT_CALL_INCOMING, lambda call_id: logger.info("CALL", f"incoming call: {call_id}"))
    service.on(EVENT_CALL_CONNECTED, lambda call_id: logger.info("CALL", f"call connected: {call_id}"))
    service.on(EVENT_CALL_ENDED, lambda call_id, last_digit="": logger.info("CALL", f"call ended: {call_id}"))
    service.on(EVENT_MEDIA_ERROR, lambda message: logger.error("MEDIA", message))

    manager.on(EVENT_DEVICES_ENUMERATED, lambda count: logger.info("AUDIO", f"enumerated {count} audio devices"))
    manager.on(EVENT_AUDIO_DEVICES_CHANGED, lambda: logger.info("AUDIO", "audio devices changed; re-enumerated"))
    manager.on(
        EVENT_DEVICE_SELECTED,
        lambda playback, capture: logger.info("AUDIO", f"device selected playback={playback} capture={capture}"),
    )
    manager.on(EVENT_PRESET_APPLIED, lambda preset: logger.info("AUDIO", f"preset applied: {preset}"))
