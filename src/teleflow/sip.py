"""SIP core service (ticket 03).

A local SIP UA boundary. The service talks to a ``SipBackend`` protocol so the
real pjsua2 transport and a ``FakeSipBackend`` (the scripted ATA gateway used in
tests) are interchangeable — that seam is the spec's "scripted SIP peer" testing
strategy. The service owns SIP/call *state* and translates raw backend events
into domain events the UI subscribes to; it performs no socket I/O itself.
"""

from __future__ import annotations

import uuid
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from teleflow.config import ConfigStore
from teleflow.tts import TtsBackend, TtsError, clean_markdown

# Domain events emitted to subscribers.
EVENT_SIP_STARTED = "sip_started"
EVENT_SIP_STOPPED = "sip_stopped"
EVENT_GATEWAY_REGISTERED = "gateway_registered"
EVENT_CALL_INCOMING = "call_incoming"
EVENT_CALL_CONNECTED = "call_connected"
EVENT_CALL_ENDED = "call_ended"
EVENT_MEDIA_ERROR = "media_error"
# Phone-report lifecycle events (feature teleflow-phone-report).
EVENT_REPORT_STARTED = "report_started"
EVENT_REPORT_CONNECTED = "report_connected"
EVENT_REPORT_PLAYING = "report_playing"
EVENT_REPORT_COMPLETED = "report_completed"
EVENT_REPORT_FAILED = "report_failed"


class CallState(str, Enum):
    IDLE = "idle"
    INCOMING = "incoming"
    CONNECTED = "connected"
    ENDED = "ended"


class ReportState(str, Enum):
    IDLE = "idle"
    DIALING = "dialing"
    PLAYING = "playing"
    COMPLETED = "completed"
    FAILED = "failed"


class ReportBusyError(Exception):
    """Raised when a report is requested while another report is in progress."""




@runtime_checkable
class SipBackend(Protocol):
    """Low-level SIP transport. Reports raw events to the handler the service
    registers, and answers/hangs up/places calls on demand."""

    def start(self, port: int, handler: Callable[[str, dict], None]) -> None: ...
    def stop(self) -> None: ...
    def answer(self, call_id: str) -> None: ...
    def hangup(self, call_id: str) -> None: ...
    def place_call(self, target: str) -> None: ...
    def place_report_call(self, target: str, wav_path: str) -> None:
        """Place an outbound *report* call (tagged): the service plays a file
        into it on connect and hangs up on playback end, rather than bridging
        to the user's sound devices."""
        ...

    def play_file_to_call(self, call_id: str, wav_path: str) -> None:
        """Play ``wav_path`` one-way into the given call (file -> call)."""
        ...

    def reroute(self) -> None:
        """Re-apply the current device selection to a live call (mid-call switch)."""
        ...

    def set_device_change_callback(self, cb: Callable[[], None]) -> None:
        """Register a callback fired when audio devices are hot-plugged."""
        ...

    def recover(self) -> None:
        """Best-effort recovery after a network drop / transport disconnect."""
        ...


class FakeSipBackend:
    """Scripted ATA gateway for tests/headless runs.

    The test drives it with ``receive_register`` / ``receive_invite`` /
    ``receive_bye`` / ``receive_media_error``; the backend invokes the service's
    handler exactly as a real UA transport would, so the service logic is
    exercised end-to-end without a network or pjsua2.
    """

    def __init__(self) -> None:
        self._handler: Callable[[str, dict], None] | None = None
        self.port: int | None = None
        self.running = False
        self.answered: list[str] = []
        self.hung_up: list[str] = []
        self.placed: list[str] = []
        self.recovered: list[str] = []
        self.rerouted: list[str] = []
        self.report_calls: list[tuple[str, str]] = []
        self.report_played: list[tuple[str, str]] = []
        self.device_change_callbacks: list[Callable[[], None]] = []

    def start(self, port: int, handler: Callable[[str, dict], None]) -> None:
        self._handler = handler
        self.port = port
        self.running = True

    def stop(self) -> None:
        self.running = False

    def _fire(self, name: str, **data: object) -> None:
        assert self._handler is not None, "backend used before start()"
        self._handler(name, data)

    # --- test hooks (the scripted ATA) ---
    def receive_register(self, contact: str) -> None:
        self._fire("register", contact=contact)

    def receive_invite(self, call_id: str) -> None:
        self._fire("invite", call_id=call_id)

    def receive_bye(self, call_id: str) -> None:
        self._fire("bye", call_id=call_id)

    def receive_media_error(self, message: str) -> None:
        self._fire("media_error", message=message)

    # --- SipBackend implementation ---
    def answer(self, call_id: str) -> None:
        self.answered.append(call_id)

    def hangup(self, call_id: str) -> None:
        self.hung_up.append(call_id)

    def place_call(self, target: str) -> None:
        self.placed.append(target)

    def place_report_call(self, target: str, wav_path: str) -> None:
        self.report_calls.append((target, wav_path))

    def play_file_to_call(self, call_id: str, wav_path: str) -> None:
        self.report_played.append((call_id, wav_path))

    def receive_report_connected(self, call_id: str) -> None:
        """Simulate the desk phone answering the report call (test hook)."""
        self._fire("report_connected", call_id=call_id)

    def receive_report_playback_done(self, call_id: str) -> None:
        """Simulate playback EOF for the report call (test hook)."""
        self._fire("report_eof", call_id=call_id)

    def reroute(self) -> None:
        # Scripted fake has no live call audio to re-route, but records the call.
        self.rerouted.append("reroute")

    def set_device_change_callback(self, cb: Callable[[], None]) -> None:
        self.device_change_callbacks.append(cb)

    def receive_device_change(self) -> None:
        """Simulate an audio-device hotplug (test hook)."""
        for cb in self.device_change_callbacks:
            cb()

    def receive_network_down(self) -> None:
        """Simulate a network / transport drop (test hook)."""
        self._fire("network_down")

    def recover(self) -> None:
        # Scripted fake records the recovery attempt; no real transport to fix.
        self.recovered.append("recovered")


class SipCoreService:
    """Local UA: accepts ATA REGISTER, stores the Contact, auto-answers INVITE,
    and emits call/media state as domain events. State resets cleanly on
    hang-up or abnormal disconnect.
    """

    def __init__(
        self,
        backend: SipBackend,
        store: ConfigStore,
        tts: TtsBackend | None = None,
    ) -> None:
        self._backend = backend
        self._store = store
        self._tts = tts
        self._state = CallState.IDLE
        self._contact: str | None = None
        self._running = False
        self._subscribers: dict[str, list[Callable[..., None]]] = {}
        # Phone-report state (feature teleflow-phone-report).
        self._report_state = ReportState.IDLE
        self._report_active = False
        self._report_id: str | None = None
        self._report_call_id: str | None = None
        self._report_wav: Path | None = None
        # Optional log sink for report sub-steps (wired by the app shell to the
        # dashboard/log view). Kept optional so headless tests stay quiet.
        self._log: Callable[..., None] | None = None

    def on(self, event: str, callback: Callable[..., None]) -> None:
        self._subscribers.setdefault(event, []).append(callback)

    def _emit(self, event: str, **data: object) -> None:
        for callback in self._subscribers.get(event, []):
            callback(**data)

    def _log_line(self, message: str) -> None:
        if self._log is not None:
            self._log(message)

    @property
    def call_state(self) -> CallState:
        return self._state

    @property
    def registered_contact(self) -> str | None:
        return self._contact

    @property
    def running(self) -> bool:
        return self._running

    @property
    def report_state(self) -> ReportState:
        return self._report_state

    @property
    def report_in_progress(self) -> bool:
        return self._report_active

    def start(self) -> None:
        port = self._store.load().sip_port
        self._backend.start(port, self._dispatch)
        self._running = True
        self._emit(EVENT_SIP_STARTED)

    def stop(self) -> None:
        self._backend.stop()
        self._running = False
        self._state = CallState.IDLE
        self._contact = None
        self._emit(EVENT_SIP_STOPPED)

    def place_call(self, target: str) -> None:
        if self._contact is None:
            raise RuntimeError("no registered gateway to call")
        self._backend.place_call(target)

    # ------------------------------------------------------------------
    # Phone-report flow (feature teleflow-phone-report).
    # Validates -> synthesizes (or uses provided wav) -> dials the desk
    # phone -> on answer plays the file one-way -> on EOF hangs up.
    # ------------------------------------------------------------------
    def start_report(
        self,
        text: str,
        *,
        audio_path: str | None = None,
        voice: str | None = None,
        target: str | None = None,
        caller_id: str | None = None,
    ) -> str:
        """Trigger a phone report. Returns a report_id; raises ReportBusyError
        if a report is already running. Failures are reported via
        EVENT_REPORT_FAILED, never by raising, so the RPC layer can map them to
        HTTP error responses."""
        if self._report_active:
            raise ReportBusyError("a report is already in progress")
        settings = self._store.load()
        if not self._running:
            self._fail_report("sip_not_running")
            raise RuntimeError("SIP service is not running")
        resolved_target = target or settings.report_target
        if not resolved_target:
            self._fail_report("no_target")
            raise RuntimeError("no report_target configured")
        if audio_path and not Path(audio_path).exists():
            self._fail_report("file_missing")
            raise RuntimeError("audio file not found")

        # Resolve the wav to play: provided file, or synthesize from text.
        try:
            wav_path = self._resolve_wav(text, audio_path, voice, settings)
        except TtsError as exc:
            reason = "ffmpeg" if "ffmpeg" in str(exc).lower() else "tts"
            self._fail_report(reason)
            raise

        report_id = uuid.uuid4().hex[:8]
        self._report_id = report_id
        self._report_active = True
        self._report_state = ReportState.DIALING
        self._report_wav = wav_path
        self._report_call_id = None
        self._log_line(f"[REPORT] 启动汇报: target={resolved_target} voice={voice or settings.tts_voice}")
        self._emit(EVENT_REPORT_STARTED, report_id=report_id, target=resolved_target)
        self._backend.place_report_call(resolved_target, str(wav_path))
        return report_id

    def _resolve_wav(self, text, audio_path, voice, settings) -> Path:
        if audio_path:
            self._log_line(f"[REPORT] 使用外部音频: {audio_path}")
            return Path(audio_path)
        tts = self._tts or self._default_tts(settings)
        voice_name = voice or settings.tts_voice
        self._log_line(f"[TTS] 合成中: voice={voice_name}")
        mp3 = tts.synthesize(clean_markdown(text), voice_name)
        self._log_line(f"[TTS] 合成完成: {mp3}")
        wav_path = tts.transcode(mp3, mp3.with_suffix(".wav"))
        self._log_line(f"[FFMPEG] 转码完成: {wav_path}")
        return wav_path

    def _default_tts(self, settings) -> TtsBackend:
        # Imported lazily so the real backend (edge-tts) is only constructed when
        # actually needed; tests inject a FakeTtsBackend instead.
        from teleflow.tts import EdgeTtsBackend

        self._tts = EdgeTtsBackend(ffmpeg_path=settings.ffmpeg_path)
        return self._tts

    def _on_report_connected(self, call_id: str) -> None:
        if not self._report_active or self._report_wav is None:
            return
        self._report_call_id = call_id
        self._report_state = ReportState.PLAYING
        self._log_line(f"[REPORT] 座机接通, 开始播放 (call {call_id})")
        self._emit(EVENT_REPORT_CONNECTED, call_id=call_id)
        self._emit(EVENT_REPORT_PLAYING, call_id=call_id)
        assert self._report_wav is not None
        self._backend.play_file_to_call(call_id, str(self._report_wav))

    def _on_report_eof(self, call_id: str) -> None:
        if not self._report_active:
            return
        self._log_line(f"[REPORT] 播放结束, 挂断 (call {call_id})")
        self._backend.hangup(call_id)
        self._report_state = ReportState.COMPLETED
        self._emit(EVENT_REPORT_COMPLETED, report_id=self._report_id or "", call_id=call_id)
        self._reset_report()

    def _fail_report(self, reason: str) -> None:
        self._log_line(f"[REPORT] 汇报失败: {reason}")
        self._report_state = ReportState.FAILED
        self._emit(EVENT_REPORT_FAILED, reason=reason, report_id=self._report_id or "")
        self._reset_report()

    def _reset_report(self) -> None:
        self._report_active = False
        self._report_id = None
        self._report_call_id = None
        self._report_wav = None

    def reroute(self) -> None:
        """Re-apply the current device selection to a live call (mid-call switch).

        The backend (real pjsua2) re-wires the conference bridge to the freshly
        selected devices; the fake is a no-op.
        """
        self._backend.reroute()

    def reroute_if_connected(self) -> None:
        """Re-route only when a call is actually active.

        Used on device hotplug: re-enumerating devices must not touch a call
        that isn't connected, and must re-wire the bridge when one is.
        """
        if self._state is CallState.CONNECTED:
            self._backend.reroute()

    def set_device_change_callback(self, cb: Callable[[], None]) -> None:
        """Register the callback the backend fires on audio-device hotplug."""
        self._backend.set_device_change_callback(cb)

    def recover(self) -> None:
        """Recover from a network / transport drop without manual intervention.

        Asks the backend to restore its path and re-announces that the service
        is up so the UI/logger reflect the recovered state.
        """
        self._backend.recover()
        self._emit(EVENT_SIP_STARTED)

    def _dispatch(self, name: str, data: dict) -> None:
        if name == "register":
            self._contact = str(data["contact"])
            self._emit(EVENT_GATEWAY_REGISTERED, contact=self._contact)
        elif name == "invite":
            call_id = str(data["call_id"])
            self._state = CallState.INCOMING
            self._emit(EVENT_CALL_INCOMING, call_id=call_id)
            self._backend.answer(call_id)
            self._state = CallState.CONNECTED
            self._emit(EVENT_CALL_CONNECTED, call_id=call_id)
        elif name in ("bye", "cancel"):
            self._state = CallState.ENDED
            self._emit(EVENT_CALL_ENDED, call_id=str(data.get("call_id", "")))
            self._state = CallState.IDLE
        elif name == "network_down":
            self.recover()
        elif name == "report_connected":
            self._on_report_connected(str(data["call_id"]))
        elif name == "report_eof":
            self._on_report_eof(str(data["call_id"]))
        elif name == "media_error":
            self._emit(EVENT_MEDIA_ERROR, message=str(data.get("message", "")))
