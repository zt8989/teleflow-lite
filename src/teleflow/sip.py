"""SIP core service (ticket 03).

A local SIP UA boundary. The service talks to a ``SipBackend`` protocol so the
real pjsua2 transport and a ``FakeSipBackend`` (the scripted ATA gateway used in
tests) are interchangeable — that seam is the spec's "scripted SIP peer" testing
strategy. The service owns SIP/call *state* and translates raw backend events
into domain events the UI subscribes to; it performs no socket I/O itself.
"""

from __future__ import annotations

import socket
import uuid
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from teleflow.config import ConfigStore, Settings
from teleflow.tts import CachingTtsBackend, TtsBackend, TtsError, clean_markdown

# Domain events emitted to subscribers.
EVENT_SIP_STARTED = "sip_started"
EVENT_SIP_STOPPED = "sip_stopped"
EVENT_SIP_REGISTERED = "sip_registered"
EVENT_SIP_UNREGISTERED = "sip_unregistered"
EVENT_SIP_REGISTER_FAILED = "sip_register_failed"
EVENT_SIP_PORT_CONFLICT = "sip_port_conflict"
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
# Inbound IVR (feature teleflow-call-ivr): emitted when the first DTMF key of an
# IVR call is pressed (the service then stops listening for further keys).
EVENT_IVR_DIGIT = "ivr_digit"
# Fired by the real backend when an inbound call's audio media becomes ACTIVE.
# IVR playback is attempted at answer time (in _maybe_start_ivr), but on the
# real pjsua2 backend the media isn't ACTIVE yet then, so play_file_to_call
# returns False; this event lets the service retry playback once it's ready.
EVENT_CALL_MEDIA_ACTIVE = "call_media_active"


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


class NoActiveCallError(Exception):
    """Raised when playback/replay is requested for a call that is not a live,
    currently-connected inbound call (e.g. already hung up or unknown id)."""




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

    def play_file_to_call(self, call_id: str, wav_path: str, *, hangup_on_eof: bool = False) -> bool:
        """Play ``wav_path`` one-way into the given call (file -> call).

        ``hangup_on_eof=True`` (report call) makes the backend hang up on EOF;
        ``False`` (IVR menu item) signals ``playback_done`` so the service can
        play the next item without hanging up. Returns ``True`` if playback was
        actually started, ``False`` if the call/media was not in a state where
        it could be played (so callers can surface that instead of silently
        doing nothing).
        """
        ...

    def stop_playback(self, call_id: str) -> None:
        """Stop an in-flight one-way playback into ``call_id`` (IVR barge-in:
        a key pressed while a prompt is still announcing cancels it). Safe to
        call when nothing is playing for that call."""
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


SIP_AUTO_PORT_START = 5060
SIP_PORT_SCAN_LIMIT = 100


def _udp_port_available(port: int) -> bool:
    """True when nothing is bound to ``port`` for UDP on this machine.

    Attempts a wildcard bind. Without extra options a plain wildcard bind can
    coexist with a concrete bind that set SO_REUSEADDR — e.g. a co-located
    registrar (FreeSWITCH) holds ``<lan-ip>:5060`` while pjsua2 would still
    bind ``0.0.0.0:5060``, and the REGISTER responses then loop back into the
    registrar. On Windows we set SO_EXCLUSIVEADDRUSE so any existing bind on
    that port (specific or wildcard, reuse or not) makes the probe fail.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            except OSError:
                pass
        sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def resolve_sip_port(
    preferred: str | int | None = None,
    *,
    probe: Callable[[int], bool] | None = None,
    start: int = SIP_AUTO_PORT_START,
    scan: int = SIP_PORT_SCAN_LIMIT,
) -> tuple[int, int | None]:
    """Decide the local UDP transport port for the SIP client.

    ``preferred`` empty/invalid means auto-detect: probe from ``start`` upward
    and take the first free port. A valid ``preferred`` is only honoured while
    free; when occupied the caller is told (second return value) and a free
    port is chosen automatically so the service still starts.

    ``probe`` defaults to the module's real availability check and is looked
    up at call time so tests can monkeypatch it.

    Returns ``(chosen_port, preferred_port_or_None)`` — when preferred is not
    None and differs from chosen, the preferred port was occupied.
    """
    if probe is None:
        probe = _udp_port_available
    requested: int | None = None
    if preferred is not None and str(preferred).strip().isdigit():
        candidate = int(str(preferred).strip())
        if 1 <= candidate <= 65535:
            requested = candidate
    if requested is not None and probe(requested):
        return requested, requested
    for candidate in range(start, start + scan):
        if probe(candidate):
            return candidate, requested
    raise RuntimeError(f"没有可用的本地 UDP 端口 (扫描范围 {start}-{start + scan - 1})")


def resolve_report_target(settings: Settings) -> str | None:
    """Build the SIP URI to dial for a phone report.

    Only ``report_extension`` (分机号) is required. When ``report_host`` (座机地址)
    is set the call goes to that desk phone —
    ``sip:{ext}@{report_host}:{report_port or 5060}`` — otherwise it defaults to
    the configured gateway (走网关) using the SIP 账号 host/port —
    ``sip:{ext}@{sip_host}:{sip_server_port}``.

    Returns ``None`` when no dialable target can be constructed: the extension is
    empty, or neither a 座机 address nor a gateway host is configured.
    """
    extension = settings.report_extension.strip()
    if not extension:
        return None
    if settings.report_host.strip():
        host = settings.report_host.strip()
        port = settings.report_port or 5060
        return f"sip:{extension}@{host}:{port}"
    gateway_host = settings.sip_host.strip()
    if not gateway_host:
        return None
    port = settings.sip_server_port or 5060
    return f"sip:{extension}@{gateway_host}:{port}"


class FakeSipBackend:
    """Scripted SIP transport for tests/headless runs.

    The test drives it with ``receive_register`` / ``receive_invite`` /
    ``receive_bye`` / ``receive_media_error``; the backend invokes the service's
    handler exactly as a real pjsua2 transport would, so the service logic is
    exercised end-to-end without a network or pjsua2. In the ``sip-softphone``
    design it additionally simulates the client-registration outcomes that
    pjsua2 reports after it registers to the external server.
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
        self.stopped_playback: list[str] = []
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

    # --- test hooks (the scripted SIP peer) ---
    def receive_register(self, contact: str | None = None) -> None:
        if contact is not None:
            self._fire("register", contact=contact)
        else:
            self._fire("register")

    def receive_unregister(self) -> None:
        self._fire("unregister")

    def receive_register_failed(self, code: int = 0, reason: str = "") -> None:
        self._fire("register_failed", code=code, reason=reason)

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

    def play_file_to_call(self, call_id: str, wav_path: str, *, hangup_on_eof: bool = False) -> bool:
        self.report_played.append((call_id, wav_path))
        # Simulate EOF immediately (no real player): report calls keep the
        # explicit receive_report_playback_done hook; IVR chains on playback_done.
        if not hangup_on_eof:
            self._fire("playback_done", call_id=call_id)
        return True

    def stop_playback(self, call_id: str) -> None:
        self.stopped_playback.append(call_id)

    def receive_report_connected(self, call_id: str) -> None:
        """Simulate the desk phone answering the report call (test hook)."""
        self._fire("report_connected", call_id=call_id)

    def receive_report_playback_done(self, call_id: str) -> None:
        """Simulate playback EOF for the report call (test hook)."""
        self._fire("report_eof", call_id=call_id)

    def receive_playback_done(self, call_id: str) -> None:
        """Simulate an IVR menu item's playback EOF (test hook). The stock fake
        fires this synchronously inside play_file_to_call; a holding fake uses
        this to end an item explicitly."""
        self._fire("playback_done", call_id=call_id)

    def receive_dtmf(self, call_id: str, digit: str) -> None:
        """Simulate an inbound DTMF key press (test hook)."""
        self._fire("dtmf", call_id=call_id, digit=digit)

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
    """Local UA: stores the registered Contact, auto-answers INVITE, and emits
    call/media state as domain events. State resets cleanly on hang-up or
    abnormal disconnect.

    In the ``sip-softphone`` design the service is a SIP *client*: the backend
    registers to an external registrar and reports ``register`` / ``unregister``
    through the same dispatch the service uses to drive the UI/logger.
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
        self._registered = False
        self._running = False
        self._subscribers: dict[str, list[Callable[..., None]]] = {}
        # Phone-report state (feature teleflow-phone-report).
        self._report_state = ReportState.IDLE
        self._report_active = False
        self._report_id: str | None = None
        self._report_call_id: str | None = None
        self._report_wav: Path | None = None
        # Inbound IVR state (feature teleflow-call-ivr): after auto-answer, play a
        # welcome message then a per-digit menu, then act on the first DTMF key.
        self._ivr_active = False
        self._ivr_call_id: str | None = None
        self._ivr_queue: list[str] = []
        self._ivr_listening = False
        self._ivr_digit_fired = False
        self._last_digit = ""
        # True once the first IVR menu item has actually started playing. Guards
        # the media-active retry so each item plays exactly once.
        self._ivr_started = False
        # Currently-connected inbound call id (set on INVITE, cleared on BYE).
        # Used to validate that ad-hoc playback/replay targets a live call.
        self._active_call_id: str | None = None
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
    def is_registered(self) -> bool:
        return self._registered

    @property
    def running(self) -> bool:
        return self._running

    @property
    def report_state(self) -> ReportState:
        return self._report_state

    @property
    def report_in_progress(self) -> bool:
        return self._report_active

    @property
    def active_call_id(self) -> str:
        """The currently-connected inbound call id, or "" when no call is up."""
        return self._active_call_id or ""

    def start(self) -> None:
        settings = self._store.load()
        # Auto-detect a free UDP transport port unless the user configured a
        # specific one; a configured-but-occupied port falls back to the next
        # free port, announcing the conflict so the UI can warn the user.
        port, requested = resolve_sip_port(settings.sip_port)
        if requested is not None and port != requested:
            self._log_line(f"[SIP] 指定端口 {requested} 已被占用，已自动改用端口 {port}")
            self._emit(EVENT_SIP_PORT_CONFLICT, requested=requested, selected=port)
        elif requested is None:
            self._log_line(f"[SIP] 自动选择本地端口 {port}")
        self._backend.start(port, self._dispatch)
        self._running = True
        self._emit(EVENT_SIP_STARTED)

    def stop(self) -> None:
        self._backend.stop()
        self._running = False
        self._state = CallState.IDLE
        self._contact = None
        self._registered = False
        self._emit(EVENT_SIP_STOPPED)

    def place_call(self, target: str) -> None:
        if not self._registered:
            raise RuntimeError("SIP client is not registered")
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
        default_target = resolve_report_target(settings)
        resolved_target = target or default_target
        if not resolved_target:
            self._fail_report("no_target")
            if not settings.report_extension.strip():
                detail = "未填写分机号（report_extension），无法拨打"
            elif target:
                detail = "指定的 target 为空"
            else:
                detail = (
                    "未配置座机地址（report_host）且未配置网关（sip_host），"
                    "无法确定拨打目标"
                )
            raise RuntimeError(f"no report target configured: {detail}")
        if audio_path and not Path(audio_path).exists():
            self._fail_report("file_missing")
            raise RuntimeError("audio file not found")

        # Resolve the wav to play: provided file, or synthesize from text.
        try:
            wav_path = self._resolve_wav(
                text, audio_path, voice, settings, prefix="report"
            )
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

    def _resolve_wav(
        self, text, audio_path, voice, settings, prefix: str = "ivr"
    ) -> Path:
        if audio_path:
            self._log_line(f"[REPORT] 使用外部音频: {audio_path}")
            return Path(audio_path)
        tts = self._tts or self._default_tts(settings)
        voice_name = voice or settings.tts_voice
        cleaned = clean_markdown(text)
        if isinstance(tts, CachingTtsBackend):
            # Route through the caching backend so the report/play prompt reuses a
            # previously rendered wav (and surfaces a [TTS] cache hit/miss line),
            # matching the inbound IVR path. 合成完成 / 转码完成 are only emitted on
            # a real miss so a cache hit isn't mislabelled as a fresh render.
            cache_file = tts._cache_dir / f"{prefix}_{CachingTtsBackend._cache_key(cleaned, voice_name)}.wav"
            was_cached = cache_file.exists()
            wav_path = tts.synthesize_to_wav(cleaned, voice_name, prefix=prefix)
            if not was_cached:
                self._log_line(f"[TTS] 合成完成: {wav_path}")
                self._log_line(f"[FFMPEG] 转码完成: {wav_path}")
            return wav_path
        # Non-caching backend (tests' FakeTtsBackend): keep the original
        # synthesize + transcode path with its own synthesis/transcode logs.
        self._log_line(f"[TTS] 合成中: voice={voice_name}")
        mp3 = tts.synthesize(cleaned, voice_name)
        self._log_line(f"[TTS] 合成完成: {mp3}")
        wav_path = tts.transcode(mp3, mp3.with_suffix(".wav"))
        self._log_line(f"[FFMPEG] 转码完成: {wav_path}")
        return wav_path

    def _default_tts(self, settings) -> TtsBackend:
        # Imported lazily so the real backend (edge-tts) is only constructed when
        # actually needed; tests inject a FakeTtsBackend instead. Wrapped in a
        # cache so IVR replays of the same welcome/menu text reuse the wav.
        from teleflow.tts import EdgeTtsBackend

        self._tts = CachingTtsBackend(
            EdgeTtsBackend(ffmpeg_path=settings.ffmpeg_path), logger=self._log_line
        )
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
        self._backend.play_file_to_call(call_id, str(self._report_wav), hangup_on_eof=True)

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

    # ------------------------------------------------------------------
    # Inbound IVR flow (feature teleflow-call-ivr).
    # After auto-answer (ivr_enabled): synthesize + queue the welcome message
    # and each digit's menu text (empty texts skipped), play them one by one,
    # then act on the first DTMF key. Keys are honoured at any time (barge-in):
    # a key pressed while a prompt is still announcing cancels the remaining
    # queue and the current playback, fires EVENT_IVR_DIGIT, and stops
    # listening for further keys. {last_digit} is surfaced on CALL_ENDED for
    # the on-hook hook.
    # ------------------------------------------------------------------
    def _build_ivr_digit_queue(self, settings: Settings) -> list[str]:
        """Synthesize the per-digit menu prompts in 1~9~0 order (empty text
        skipped). Each item is announced as ``"{text} 请按{digit}"`` so the
        caller hears the option first and then which key triggers it.
        """
        assert self._tts is not None  # callers guard _tts before queueing playback
        queue: list[str] = []
        for digit in "1234567890":
            text = settings.ivr_digit_text.get(digit, "").strip()
            if not text:
                continue
            prompt = f"{text} 请按{digit}"
            queue.append(str(self._tts.synthesize_to_wav(prompt, settings.tts_voice)))
        return queue

    def _maybe_start_ivr(self, call_id: str) -> None:
        settings = self._store.load()
        if not settings.ivr_enabled or self._tts is None:
            return
        voice = settings.tts_voice
        # Synthesize first so a TTS failure leaves the call as a normal two-way
        # bridge instead of a silently-mic-suppressed call.
        try:
            queue: list[str] = []
            if settings.ivr_welcome.strip():
                queue.append(str(self._tts.synthesize_to_wav(settings.ivr_welcome, voice)))
            queue.extend(self._build_ivr_digit_queue(settings))
        except TtsError as exc:
            self._log_line(f"[IVR] TTS 失败, 跳过 IVR: {exc}")
            return
        # TTS succeeded: start the menu. The call is bridged two-way throughout
        # (the mic is never suppressed during IVR), so the AI side can talk back
        # or barge in at any time.
        self._ivr_active = True
        self._ivr_call_id = call_id
        self._ivr_queue = queue
        self._ivr_listening = False
        self._ivr_digit_fired = False
        self._last_digit = ""
        self._log_line(f"[IVR] 启动菜单: call={call_id} 条目数={len(queue)}")
        self._ivr_play_next()

    def _ivr_play_next(self) -> None:
        if not self._ivr_active or self._ivr_call_id is None:
            return
        if not self._ivr_queue:
            # Menu queue drained. Keys are honoured at any time (see _on_dtmf),
            # so this flag is informational here.
            self._ivr_listening = True
            self._log_line(f"[IVR] 菜单播报完成 (call {self._ivr_call_id})")
            return
        # Pop the front item. The fake backend starts playback and fires
        # playback_done synchronously, so on success the chain advances to the
        # next item. On the real pjsua2 backend the audio media is usually not
        # ACTIVE yet at answer time, so play_file_to_call returns False: re-queue
        # the item at the front so the media-active retry can replay it (instead
        # of silently dropping it as a naive pop-then-play would).
        wav = self._ivr_queue.pop(0)
        started = self._backend.play_file_to_call(self._ivr_call_id, wav, hangup_on_eof=False)
        if not started:
            self._ivr_queue.insert(0, wav)
            return
        self._ivr_started = True

    def _on_ivr_playback_done(self, call_id: str) -> None:
        if not self._ivr_active or call_id != self._ivr_call_id:
            return
        self._ivr_play_next()

    def _on_call_media_active(self, call_id: str) -> None:
        # The real backend signals this once an inbound call's audio media is
        # up. IVR playback may have been attempted (and failed) at answer time;
        # retry the front of the queue now. Only triggers once — once anything
        # has started playing the playback_done chain owns the rest, and once a
        # key has fired (barge-in canceled the queue) the menu must not be
        # resurrected by a late media-active event.
        if not self._ivr_active or call_id != self._ivr_call_id:
            return
        if self._ivr_started or self._ivr_digit_fired:
            return
        self._ivr_play_next()

    # ------------------------------------------------------------------
    # Ad-hoc playback / menu replay (RPC-driven, used by per-digit IVR hooks).
    # ------------------------------------------------------------------
    def _is_active_call(self, call_id: str) -> bool:
        return self._state is CallState.CONNECTED and call_id == self._active_call_id

    def play_to_call(
        self,
        call_id: str,
        *,
        text: str | None = None,
        audio_path: str | None = None,
        voice: str | None = None,
        hangup_on_eof: bool = False,
    ) -> None:
        """Play a prompt (TTS-synthesized or a provided WAV) one-way into a live
        inbound call.

        Driven by the per-digit IVR hook via ``POST /v1/play`` so a digit key's
        command can speak back into the call that is currently connected. There
        must be a currently-connected inbound call for ``call_id`` — otherwise
        ``NoActiveCallError`` is raised so the RPC layer can return 404 rather
        than silently playing into a dead/unknown call.
        """
        if not self._is_active_call(call_id):
            raise NoActiveCallError(f"no active call for call_id={call_id!r}")
        settings = self._store.load()
        text = "" if text is None else str(text)
        if not text and not audio_path:
            raise ValueError("text or audio_path required")
        if audio_path and not Path(audio_path).exists():
            raise FileNotFoundError(f"audio file not found: {audio_path}")
        wav_path = self._resolve_wav(text, audio_path, voice, settings, prefix="ivr")
        started = self._backend.play_file_to_call(call_id, str(wav_path), hangup_on_eof=hangup_on_eof)
        if not started:
            # Call exists but its media isn't in a playable state (should not
            # happen for the always-connected use case); surface it rather than
            # pretend the playback succeeded.
            raise RuntimeError(f"playback could not start for call_id={call_id!r}")

    def replay_ivr_menu(self, call_id: str) -> None:
        """Return an active IVR call to its digit menu: re-announce the 1~9~0
        prompts and resume listening, so the caller can press another key.

        Only valid while an IVR call is active for ``call_id``; otherwise raises
        ``NoActiveCallError`` (mapped to HTTP 404 by the RPC layer).
        """
        if not (self._ivr_active and call_id == self._ivr_call_id):
            raise NoActiveCallError(f"no active IVR call for call_id={call_id!r}")
        if self._tts is None:
            raise NoActiveCallError("ivr tts not initialized")
        settings = self._store.load()
        queue = self._build_ivr_digit_queue(settings)
        self._ivr_digit_fired = False
        self._ivr_listening = False
        self._ivr_started = False
        self._ivr_queue = queue
        self._log_line(f"[IVR] 重播菜单: call={call_id} 条目数={len(queue)}")
        self._ivr_play_next()

    def _on_dtmf(self, call_id: str, digit: str) -> None:
        if not self._ivr_active or call_id != self._ivr_call_id:
            return
        if self._ivr_digit_fired:
            return
        self._ivr_digit_fired = True
        self._ivr_listening = False
        self._last_digit = digit
        if self._ivr_queue:
            # Barge-in: a key pressed while the menu is still announcing wins
            # over the remaining prompts. Cancel the queue and stop the current
            # playback so the caller's choice isn't drowned out by the menu tail.
            self._ivr_queue = []
            self._backend.stop_playback(call_id)
        self._log_line(f"[IVR] 收到按键 {digit} (call {call_id})")
        # Every digit fires its per-digit hook; the call stays bridged two-way
        # (the mic is never suppressed during IVR), so the AI side can talk back
        # or barge in at any time. IVR menu mode ends only when the call hangs up.
        self._emit(EVENT_IVR_DIGIT, call_id=call_id, digit=digit)

    def _reset_ivr(self) -> None:
        self._ivr_active = False
        self._ivr_call_id = None
        self._ivr_queue = []
        self._ivr_listening = False
        self._ivr_digit_fired = False
        self._last_digit = ""
        self._ivr_started = False

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
            # The backend may report a Contact (real pjsua2) or none (scripted
            # fake); either way registration succeeded.
            self._contact = data.get("contact")
            self._registered = True
            self._emit(EVENT_SIP_REGISTERED, contact=self._contact or "")
        elif name == "unregister":
            self._contact = None
            self._registered = False
            self._emit(EVENT_SIP_UNREGISTERED)
        elif name == "register_failed":
            self._contact = None
            self._registered = False
            self._emit(
                EVENT_SIP_REGISTER_FAILED,
                code=int(data.get("code", 0)),
                reason=str(data.get("reason", "")),
            )
        elif name == "invite":
            call_id = str(data["call_id"])
            self._state = CallState.INCOMING
            self._emit(EVENT_CALL_INCOMING, call_id=call_id)
            self._backend.answer(call_id)
            self._state = CallState.CONNECTED
            self._active_call_id = call_id
            self._emit(EVENT_CALL_CONNECTED, call_id=call_id)
            self._maybe_start_ivr(call_id)
        elif name in ("bye", "cancel"):
            self._state = CallState.ENDED
            self._emit(
                EVENT_CALL_ENDED, call_id=str(data.get("call_id", "")), last_digit=self._last_digit
            )
            self._state = CallState.IDLE
            self._active_call_id = None
            self._reset_ivr()
        elif name == "dtmf":
            self._on_dtmf(str(data["call_id"]), str(data["digit"]))
        elif name == "playback_done":
            self._on_ivr_playback_done(str(data["call_id"]))
        elif name == "network_down":
            self.recover()
        elif name == "report_connected":
            self._on_report_connected(str(data["call_id"]))
        elif name == "report_eof":
            self._on_report_eof(str(data["call_id"]))
        elif name == "call_media_active":
            self._on_call_media_active(str(data["call_id"]))
        elif name == "media_error":
            self._emit(EVENT_MEDIA_ERROR, message=str(data.get("message", "")))
