"""Real SIP UA transport backed by the pjsua2 native library (ticket 04).

Implements the ``SipBackend`` protocol consumed by ``SipCoreService``. pjsua2 is
imported lazily so this module stays importable (and its import guard
unit-testable) without the native library present; everything that actually
touches pjsua2 is ``pragma: no cover`` because it can only run where the native
extension is built and installed.

Audio routing model
-------------------
TeleFlow is a *pure* audio router. On answer we select the user's chosen
playback/capture devices on pjsua2's audio device manager (``MediaBridge``), and
when the call's media becomes active we connect the call's audio media to that
device through pjsua2's conference bridge — downstream to the playback device,
upstream from the capture device. No recorder and no DSP stage is inserted,
which is exactly the lossless, no-recording, no-DSP guarantee of ticket 04.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable

from teleflow.config import ConfigStore
from teleflow.media import AudioDeviceController, MediaBridge, capture_device_selected
from teleflow.sip import SipBackend


# pjsua2's ``Endpoint`` is a process-wide singleton: constructing a second
# ``pj.Endpoint()`` aborts the process with ``PJ_EEXISTS`` (uncatchable SIGABRT).
# Both the audio backend (device enumeration) and this SIP backend need the same
# singleton, so we construct it exactly once here as the callback-bearing
# subclass and hand the same instance to everyone. ``_shared_backend`` wires the
# singleton's callbacks back to the live (single) SIP backend instance.
_shared_endpoint: Any | None = None
_shared_backend: "Pjsua2Backend | None" = None


def get_shared_endpoint(pj: Any) -> Any:  # pragma: no cover - native only
    """Return the one process-wide ``pjsua2.Endpoint`` instance.

    The instance is built as a subclass so the audio-device and transport-state
    callbacks can be routed to the live backend without anyone else constructing
    a second (fatal) Endpoint.
    """

    global _shared_endpoint
    if _shared_endpoint is None:

        class _Endpoint(pj.Endpoint):  # type: ignore[misc, valid-type]
            def onAudioDevState(self, prm: Any) -> None:  # noqa: ARG002
                if (
                    _shared_backend is not None
                    and _shared_backend._device_change_cb is not None
                ):
                    _shared_backend._device_change_cb()

            def onTransportState(self, prm: Any) -> None:  # noqa: ARG002
                state = getattr(prm, "state", None)
                if (
                    state == pj.PJSIP_TP_STATE_DISCONNECTED
                    and _shared_backend is not None
                    and _shared_backend._handler is not None
                ):
                    _shared_backend._handler("network_down", {})

        _shared_endpoint = _Endpoint()
    return _shared_endpoint


class Pjsua2AudioController:
    """Thin adapter from ``AudioDeviceController`` to pjsua2's ``audDevManager``.

    pjsua2 device indices are integers; our persisted ids are numeric strings
    (see ``PortAudioBackend`` enumeration), so the coercion happens here and the
    rest of the code stays device-id-agnostic.
    """

    def __init__(self, manager: Any, pj: Any) -> None:
        self._mgr = manager
        self._pj = pj

    def set_playback_device(self, device_id: str) -> None:  # pragma: no cover
        self._mgr.setPlaybackDev(int(device_id))

    def set_capture_device(self, device_id: str) -> None:  # pragma: no cover
        self._mgr.setCaptureDev(int(device_id))


def _registrar_uri(host: str, port: int) -> str:
    """Compose the registrar URI from the gateway host/port settings."""
    return f"sip:{host}:{port}"


def _make_classes(pj: Any, backend: "Pjsua2Backend") -> tuple[type, type]:
    """Build the pjsua2 Account/Call subclasses wired to this backend."""

    def _audio_media_active(info: Any) -> bool:
        """True when the call's media list has an ACTIVE audio stream.

        Early media (a 183 with SDP) already activates the stream while the
        peer is still ringing, so alone this must never mean "the phone was
        picked up"; callers combine it with the INVITE state (CONFIRMED).
        """
        return any(
            m.type == pj.PJMEDIA_TYPE_AUDIO
            and m.status == pj.PJSUA_CALL_MEDIA_ACTIVE
            for m in info.media
        )

    class Call(pj.Call):  # type: ignore[misc, valid-type]
        def __init__(self, account: Any, call_id: int = -1) -> None:
            # For an inbound call the object must be constructed with the
            # real incoming call id; pjsua2 only attaches this Call to the
            # SIP call (and gives it a usable id) when call_id is valid — see
            # Call::Call in pjsua2's call.cpp, which sets the call's user
            # data only when call_id != PJSUA_INVALID_ID. Outbound calls
            # pass the default so makeCall assigns the id itself.
            pj.Call.__init__(self, account, call_id)

        def onCallState(self, prm: Any) -> None:  # noqa: ARG002 - prm unused
            info = self.getInfo()
            if (
                getattr(self, "_is_report", False)
                and info.state == pj.PJSIP_INV_STATE_CONFIRMED
            ):
                # Early media (183 w/ SDP) activates the media while the phone
                # is still ringing; playback must not start until the peer
                # actually answers (200/INVITE -> CONFIRMED), or the caller
                # hears the middle of the report when they pick up.
                self._report_call_confirmed = True
                if (
                    not getattr(self, "_report_connected_fired", False)
                    and _audio_media_active(info)
                    and backend._handler is not None
                ):
                    self._report_connected_fired = True
                    backend._handler(
                        "report_connected", {"call_id": str(info.id)}
                    )
            if info.state != pj.PJSIP_INV_STATE_DISCONNECTED:
                return
            call_id = str(info.id)
            backend._calls.pop(call_id, None)
            # Any call that played a file (report or IVR menu) owns a player that
            # must be released once the call is torn down, so the underlying C++
            # object is freed.
            if call_id in backend._report_players:
                _release_report_player(backend, call_id)
            # Tell the service the call ended so it can reset its call state;
            # otherwise the UI stays stuck on "通话中" after a hang-up. pjsua2
            # dispatches this callback to the attached Call object once the
            # call is torn down. A report call drives its own lifecycle via
            # "report_eof", so it must not emit the normal call-ended event;
            # but a report call that never connected (busy / no answer /
            # rejected) has no EOF coming, so it signals "report_disconnected"
            # instead and the service resets the report slot.
            if getattr(self, "_is_report", False):
                # Always signal teardown when a report call disconnects. The
                # player's onEof2 normally drives completion via "report_eof",
                # but if the call tears down before EOF ever arrives — the file
                # ended without the callback firing, the peer hung up mid-
                # playback, or the network dropped — "report_eof" will never
                # come and the service would leave its report slot wedged as
                # "in progress", so every later /v1/report is rejected with 409
                # until the app restarts. The service ignores this once the
                # report has already been reset (guarded by `if self._report_active`),
                # so emitting unconditionally is safe and closes the wedge.
                if backend._handler is not None:
                    backend._handler("report_disconnected", {"call_id": call_id})
            elif backend._handler is not None:
                backend._handler("bye", {"call_id": call_id})

        def onCallMediaState(self, prm: Any) -> None:  # noqa: ARG002 - prm unused
            info = self.getInfo()
            for media in info.media:
                if not (
                    media.type == pj.PJMEDIA_TYPE_AUDIO
                    and media.status == pj.PJSUA_CALL_MEDIA_ACTIVE
                ):
                    continue
                # A report call is played into (one-way) by the service on
                # connect; it must NOT be bridged to the user's sound devices.
                # Signal connect exactly once — only after the call was truly
                # answered (CONFIRMED), never on early media (183) — and let
                # the service drive playback.
                if getattr(self, "_is_report", False):
                    if (
                        not getattr(self, "_report_connected_fired", False)
                        and getattr(self, "_report_call_confirmed", False)
                        and backend._handler is not None
                    ):
                        self._report_connected_fired = True
                        backend._handler(
                            "report_connected", {"call_id": str(info.id)}
                        )
                    return
                # An IVR call is announced one-way (file -> call) by the service;
                # the mic is suppressed (no bridge) so the menu can't echo. Tell
                # the service the media is active so it can (re)start playback;
                # a plain call ignores that event, so firing it is harmless.
                if getattr(self, "_is_ivr", False):
                    if backend._handler is not None:
                        backend._handler(
                            "call_media_active", {"call_id": str(info.id)}
                        )
                    return
                # Normal inbound call (or an IVR call whose bridge was restored
                # by unmark_ivr): bridge two-way. Tell the service the media is
                # active so it can (re)start IVR playback; a plain call ignores
                # that event, so firing it is harmless.
                if backend._handler is not None:
                    backend._handler(
                        "call_media_active", {"call_id": str(info.id)}
                    )
                _bridge_two_way(backend, self, media.index)
                # A pjsua2 call has a single audio stream; bridge it once.
                return

        def onDtmfDigit(self, prm: Any) -> None:  # noqa: ARG002 - pjsua2 callback signature
            # Forward an inbound DTMF keypress to the service so the IVR can act
            # on it. pjsua2 passes the digit via ``OnDtmfDigitParam.digit`` (an
            # int ASCII code); defensively convert to a single-character string.
            digit = getattr(prm, "digit", None)
            if digit is None:
                return
            if isinstance(digit, int):
                digit = chr(digit)
            digit = str(digit)
            if backend._handler is not None:
                backend._handler("dtmf", {"call_id": str(self.getId()), "digit": digit})

    class Account(pj.Account):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            pj.Account.__init__(self)

        def onIncomingCall(self, prm: Any) -> None:
            # Attach the Call object to the real incoming call id so the
            # service-driven backend.answer() (and audio bridging in
            # onCallMediaState) can actually reach the caller. Without this the
            # Call is detached and the inbound call can never be answered.
            call = Call(self, call_id=int(prm.callId))
            call_id = str(prm.callId)
            backend._calls[call_id] = call
            # Do NOT answer here: SipCoreService drives the answer via
            # backend.answer(call_id) so the same code path serves the fake
            # and the real backend.
            if backend._handler is not None:
                backend._handler("invite", {"call_id": call_id})

        def onRegState(self, prm: Any) -> None:  # noqa: ARG002 - prm unused
            # Report client-registration outcomes to the SIP core so it can
            # drive the "SIP 注册" state. pjsua2 fires this on every REGISTER
            # transaction: code 200 + expiration>0 = registered, code 200 +
            # expiration 0 = unregistered. 401/407 are digest-auth challenges
            # pjsua2 answers itself; the real outcome arrives in a subsequent
            # onRegState, so we must not surface them as a failure or the UI
            # flashes a spurious error before the 200 OK lands.
            if backend._handler is None:
                return
            code = getattr(prm, "code", 0)
            expiration = getattr(prm, "expiration", 0)
            if code == 200 and expiration > 0:
                # Forward the contact URI so the log line "SIP registered:
                # <contact>" is populated. The field name varies across pjsua2
                # builds: newer ones expose ``regContactURI`` on AccountInfo,
                # while this 2.17 build only exposes ``uri`` (the account's
                # bare AOR, e.g. "sip:1002@192.168.1.189" with no port or
                # transport). getInfo() can also throw if the account isn't
                # fully ready, so read everything defensively.
                contact = ""
                try:
                    info = self.getInfo()
                    contact = (
                        getattr(info, "regContactURI", "")
                        or getattr(info, "uri", "")
                        or ""
                    )
                except Exception:  # noqa: BLE001 - never let a register callback crash
                    contact = ""
                if not contact:
                    try:
                        contact = getattr(self.cfg, "idUri", "") or ""
                    except Exception:  # noqa: BLE001
                        contact = ""
                # The bare AOR lacks the bound transport port/protocol that the
                # registrar echoes back (e.g. "...:5061;transport=UDP"). Enrich
                # it so the log shows the real contact. backend.port is the port
                # transportCreate() actually bound (auto-selected when the
                # preferred one was taken), and the only transport we create is
                # UDP.
                if contact and getattr(backend, "port", None):
                    m = re.match(r"^(sips?):([^@;?]+)@([^;?]+)", contact, re.IGNORECASE)
                    if m:
                        scheme, user, host = m.group(1), m.group(2), m.group(3).split(":")[0]
                        contact = f"{scheme}:{user}@{host}:{backend.port};transport=UDP"
                backend._handler("register", {"contact": contact})
            elif code == 200 and expiration == 0:
                backend._handler("unregister", {})
            elif code not in (401, 407):
                backend._handler(
                    "register_failed",
                    {"code": code, "reason": getattr(prm, "reason", "")},
                )

    return Call, Account


def _make_report_player(pj: Any, backend: "Pjsua2Backend", call_id: str, *, hangup_on_eof: bool = False) -> Any:  # pragma: no cover
    """Build a pjsua2 ``AudioMediaPlayer`` subclass whose virtual ``onEof2``
    tells the backend playback finished.

    ``hangup_on_eof=True`` (report call): emit ``report_eof`` so the service
    hangs up. ``hangup_on_eof=False`` (IVR menu item): emit ``playback_done``
    so the service plays the next menu item — no hang-up.

    This binding predates ``AudioMediaPlayer.setEOFCallback``; the supported
    mechanism is to subclass the player and override the ``onEof2`` director
    method. Native-only.
    """

    class ReportPlayer(pj.AudioMediaPlayer):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            pj.AudioMediaPlayer.__init__(self)
            self._backend = backend
            self._call_id = call_id
            self._sink: Any = None
            self._eof_fired = False
            self._hangup_on_eof = hangup_on_eof

        def onEof2(self) -> None:  # noqa: ARG002 - pjsua2 callback signature
            if self._eof_fired:
                return
            self._eof_fired = True
            # Release the conference slot before reporting EOF; destroying the
            # player while a transmission is still live can crash pjsua2.
            if self._sink is not None:
                try:
                    self.stopTransmit(self._sink)
                except Exception:  # noqa: BLE001 - best-effort
                    pass
            if self._backend._handler is None:
                return
            if self._hangup_on_eof:
                self._backend._handler("report_eof", {"call_id": self._call_id})
            else:
                self._backend._handler("playback_done", {"call_id": self._call_id})

    return ReportPlayer()


def _release_report_player(backend: "Pjsua2Backend", call_id: str) -> None:  # pragma: no cover
    """Drop our reference to a report player once its call is gone."""
    backend._report_players.pop(call_id, None)


def _bridge_two_way(backend: "Pjsua2Backend", call: Any, media_index: int) -> None:  # pragma: no cover
    """Connect a call's active audio media to the user's sound devices through
    pjsua2's conference bridge: downstream call -> playback device, upstream
    capture device -> call. No recorder / transform is inserted."""
    call_audio = call.getAudioMedia(media_index)
    dev_mgr = backend._ep.audDevManager()
    # Downstream: decoded call audio -> selected playback device.
    call_audio.startTransmit(dev_mgr.getPlaybackDevMedia())
    # Upstream: selected capture device -> call audio (to telephone). Only when a
    # capture device is actually selected — an empty capture id is one-way
    # (downstream only), matching MicroSIP, so we must NOT open a capture
    # endpoint or the OS microphone prompt fires and we'd transmit silence back.
    cap = backend._store.load().capture_device_id
    bridge = backend._bridge
    if capture_device_selected(cap) and bridge is not None:
        bridge.apply_capture(cap)
        dev_mgr.getCaptureDevMedia().startTransmit(call_audio)


def _new_call_op(pj: Any) -> Any:
    """Build a CallOpParam with the media settings every outbound/inbound call
    needs.

    pjsua2's CallSetting.isEmpty() is true when audio/video/text counts are
    all zero, and makeCall() then falls back to the pjsua default setting —
    which enables a T.140 text media (``m=text`` in the SDP). Cheap gateways
    (e.g. the NewRockTech ATA) reject that offer with 415 Unsupported Media
    Type and the phone never rings. Explicitly requesting 1 audio stream and
    0 text streams keeps the setting non-empty and the text line out of the
    SDP.
    """
    op = pj.CallOpParam()
    op.opt.audioCount = 1
    op.opt.textCount = 0
    op.opt.videoCount = 0
    return op


def _ep_config(pj: Any, log_file: str | None = None) -> Any:
    """Shared ``EpConfig`` for the process-wide pjsua2 endpoint.

    The audio layer and the SIP backend both create/init the shared endpoint;
    whichever runs first wins, so both must apply the same config. In
    particular pjsua2's C-side logging holds its internal write lock; another
    thread making a pjsua2 API call (which also logs) then blocks on it while
    the writer waits on the pjsua mutex the caller holds — a deadlock that
    froze the app at every report EOF and on the outbound 407. Directing the
    log to a dedicated file keeps that lock off the shared stdio stream.
    """
    cfg = pj.EpConfig()
    if log_file is not None:
        cfg.logConfig.level = 5
        cfg.logConfig.consoleLevel = 0
        try:
            cfg.logConfig.filename = log_file
        except Exception:  # noqa: BLE001 - logging stays best-effort
            pass
    if sys.platform == "darwin":
        # macOS 音频卡顿修复（Windows/WMME 无此问题）：
        # 1) pjsua2 默认 ecTailLen=200 会让 CoreAudio 后端启用
        #    VoiceProcessingIO —— 苹果的语音处理单元（AEC/AGC/降噪），它
        #    忽略所选声卡、以 16k 客户端驱动 48k 设备。关掉 EC 让它退回
        #    HALOutput 并尊重 Settings 里选的播放/采集设备。
        # 2) 主时钟对齐设备原生 48k：端点不再做 16k<->48k 重采样，也少走
        #    WSOLA delay buffer；8k TTS WAV 只经一次重采样进入通话。
        # 3) 媒体时钟由 pjsua 的 worker 线程(定时器堆)驱动；单 worker 在
        #    macOS 上会被 GUI/GIL/Python 回调拖慢，表现为发出的 RTP
        #    周期性地抖动/停发（RTCP 显示 TX jitter 124-188ms、丢失 1-7%）。
        #    多开几个 GIL-free 的 C worker 让定时器堆总能被准时轮询。
        # 4) pjsua 默认 VAD(静音检测)开启：菜单播报间隙/空闲时完全停发
        #    RTP，每次恢复发送的边界接收端 jitter buffer 恰好被掏空 →
        #    每段提示音开头卡一下。noVad 让流持续发送（静音也发 RTP），
        #    消除"发-停"边界。
        cfg.medConfig.ecTailLen = 0
        cfg.medConfig.ecOptions = 0
        cfg.medConfig.clockRate = 48000
        cfg.medConfig.sndClockRate = 48000
        cfg.uaConfig.threadCnt = 3
        cfg.medConfig.noVad = True
    return cfg


class Pjsua2Backend:
    """Native SIP backend. Construction fails fast if pjsua2 is absent."""

    def __init__(self, store: ConfigStore) -> None:
        try:
            import pjsua2 as pj
        except ImportError as exc:
            raise RuntimeError(
                "pjsua2 native library is required for the real SIP backend"
            ) from exc
        self._pj = pj
        self._store = store
        self._handler: Callable[[str, dict], None] | None = None
        self._transport: Any = None
        self._account: Any = None
        self._bridge: MediaBridge | None = None
        self._calls: dict[str, Any] = {}
        # Report players must outlive playback; pjsua2's conference bridge only
        # holds a port reference, not the Python director object, so we keep the
        # player alive here until its EOF fires and the call is torn down.
        self._report_players: dict[str, Any] = {}
        self._lib_created = False
        self._call_cls: type | None = None
        self._account_cls: type | None = None
        self._device_change_cb: Callable[[], None] | None = None
        self.running = False
        self.port: int | None = None

        # Use the shared, process-wide Endpoint (built as the callback subclass
        # above). This also makes the singleton's callbacks route back to us.
        global _shared_backend
        _shared_backend = self
        self._ep = get_shared_endpoint(pj)

    def _ensure_lib(self) -> None:  # pragma: no cover
        if self._lib_created:
            return
        # ``libGetState()`` returns 0 (not created), 1 (created), 2 (initialized).
        # The audio backend may have already initialized the shared lib during
        # device enumeration, so only create/init what is missing.
        if self._ep.libGetState() == 0:
            self._ep.libCreate()
        if self._ep.libGetState() < 2:
            self._ep.libInit(
                _ep_config(
                    self._pj,
                    log_file=str(Path.home() / ".config" / "teleflow" / "pjsua2.log"),
                )
            )
        mgr = self._ep.audDevManager()
        self._bridge = MediaBridge(Pjsua2AudioController(mgr, self._pj))
        self._call_cls, self._account_cls = _make_classes(self._pj, self)
        self._lib_created = True

    def _apply_route(self) -> None:  # pragma: no cover
        if self._bridge is None:
            return
        settings = self._store.load()
        pb, cap = settings.playback_device_id, settings.capture_device_id
        # An empty / "-1" selection means "no concrete device chosen yet". Apply
        # each direction independently so selecting only one device still takes
        # effect; we never forward "-1" to pjsua2, which would disable audio.
        if pb not in ("", "-1", None):
            self._bridge.apply_playback(pb)
        # A capture device is only opened when one is actually selected. An empty
        # capture id is one-way (downstream only), matching MicroSIP: opening no
        # capture endpoint keeps the OS microphone prompt from firing. We pin the
        # capture device to the null sink so getCaptureDevMedia() can never fall
        # back to the default microphone and open a real input stream.
        if capture_device_selected(cap):
            self._bridge.apply_capture(cap)
        else:
            try:
                self._ep.audDevManager().setCaptureDev(self._pj.PJSUA_SND_NULL_DEV)
            except Exception:  # noqa: BLE001 - best-effort; downstream still bridges
                pass

    def start(self, port: int, handler: Callable[[str, dict], None]) -> None:  # pragma: no cover
        self._handler = handler
        self._ensure_lib()
        self._apply_route()

        tcfg = self._pj.TransportConfig()
        tcfg.port = port
        self._transport = self._ep.transportCreate(
            self._pj.PJSIP_TRANSPORT_UDP, tcfg
        )
        self._ep.libStart()

        if sys.platform == "darwin":
            # Prefer Opus/48k so an Opus-first softphone (linphone, its Android
            # sibling) negotiates Opus end-to-end and FreeSWITCH bridges the two
            # legs without transcoding. When teleflow was limited to 8k PCMU,
            # FreeSWITCH had to transcode 8k PCMU <-> 48k Opus, and its
            # write-resampler filled the IVR's word gaps with noise — the user
            # heard "正文持续卡" on inbound IVR while outbound reports (PCMU on
            # both legs) stayed clean. G722/16k is a fallback; the narrowband-only
            # speex/iLBC/GSM are disabled so they can never win negotiation.
            self._ep.codecSetPriority("OPUS", 255)
            self._ep.codecSetPriority("G722", 250)
            for codec_id in ("speex/8000", "speex/16000", "speex/32000", "iLBC/8000", "GSM/8000"):
                try:
                    self._ep.codecSetPriority(codec_id, 0)
                except Exception:  # noqa: BLE001 - absent codecs are fine
                    pass

        acc_cfg = self._pj.AccountConfig()
        settings = self._store.load()
        # Local account URI (the AOR). Anchor the host to the configured
        # gateway when present so the identity matches the server's realm;
        # otherwise fall back to a local peer identity (direct IP calling).
        host = settings.sip_host or "localhost"
        acc_cfg.idUri = f"sip:{settings.sip_user or 'teleflow'}@{host}"
        # Register to the configured gateway as a client, with digest auth.
        if settings.sip_host:
            acc_cfg.regConfig.registrarUri = _registrar_uri(
                settings.sip_host, settings.sip_server_port
            )
            acc_cfg.regConfig.register = True
            if settings.sip_user:
                cred = self._pj.AuthCredInfo(
                    "digest", "*", settings.sip_user, 0, settings.sip_password
                )
                acc_cfg.sipConfig.authCreds.append(cred)
        assert self._account_cls is not None
        self._account = self._account_cls()
        self._account.create(acc_cfg)
        self.port = port
        self.running = True

    def stop(self) -> None:  # pragma: no cover
        if self._lib_created:
            try:
                self._ep.libDestroy()
            except Exception:
                pass
            self._lib_created = False
        self.running = False

    def answer(self, call_id: str) -> None:  # pragma: no cover
        # Re-apply the route so a mid-session device change is honoured, then
        # answer with audio. pjsua2's conference bridge (wired in onCallMediaState)
        # connects the call to the sound device already selected above.
        self._apply_route()
        call = self._calls.get(call_id)
        if call is not None:
            op = _new_call_op(self._pj)
            op.statusCode = 200
            call.answer(op)

    def hangup(self, call_id: str) -> None:  # pragma: no cover
        call = self._calls.get(call_id)
        if call is not None:
            # Call.hangup() has no default for prm either (same SWIG quirk as
            # Call.makeCall's prm): omitting it is a TypeError, which used to be
            # swallowed by the EOF callback and left the report slot wedged.
            op = _new_call_op(self._pj)
            call.hangup(op)

    def place_call(self, target: str) -> None:  # pragma: no cover
        self._apply_route()
        assert self._call_cls is not None
        call = self._call_cls(self._account)
        op = _new_call_op(self._pj)
        call.makeCall(target, op)
        # Keep a reference: pjsua2 hangs up a call whose Python wrapper is
        # garbage-collected ("Call 0 hanging up" right after makeCall).
        self._calls[str(call.getId())] = call

    def place_report_call(self, target: str, wav_path: str) -> None:  # pragma: no cover
        # Like place_call, but the Call instance is tagged as a report call so
        # onCallMediaState signals "report_connected" (instead of bridging) and
        # the service plays the file into it on answer. A report call is
        # one-way playback into the call; it must NOT touch the user's sound
        # devices, so unlike a normal call we skip _apply_route() here (it
        # would open the capture device and show "microphone in use").
        assert self._call_cls is not None
        call = self._call_cls(self._account)
        call._is_report = True
        call._report_file = wav_path
        call._report_connected_fired = False
        call._report_call_confirmed = False
        op = _new_call_op(self._pj)
        call.makeCall(target, op)
        # Keep a reference (see place_call: GC of the wrapper hangs up the call).
        self._calls[str(call.getId())] = call
        # pjsua2 opens the sound devices while initialising the call's media;
        # a report call only plays a file into the call and must not claim the
        # microphone. Drop the capture device (playback is irrelevant here
        # too; the next normal call's _apply_route restores the real device).
        try:
            self._ep.audDevManager().setCaptureDev(self._pj.PJSUA_SND_NULL_DEV)
        except Exception:  # noqa: BLE001 - best-effort; the call still works
            pass

    def play_file_to_call(self, call_id: str, wav_path: str, *, hangup_on_eof: bool = False) -> bool:  # pragma: no cover
        # One-way playback: file -> call audio only. No capture device, no
        # recorder. The player subclassed below fires ``onEof2`` when the file
        # ends: for a report call that signals EOF (service hangs up); for an
        # IVR menu item it signals ``playback_done`` (service plays the next).
        call = self._calls.get(call_id)
        if call is None:
            return False
        info = call.getInfo()
        media_index = None
        for media in info.media:
            if (
                media.type == self._pj.PJMEDIA_TYPE_AUDIO
                and media.status == self._pj.PJSUA_CALL_MEDIA_ACTIVE
            ):
                media_index = media.index
                break
        if media_index is None:
            return False
        call_audio = call.getAudioMedia(media_index)
        # Drop any previous player for this call (a finished IVR menu item); the
        # previous one already fired EOF and released its sink, so losing the
        # reference is safe.
        self._report_players.pop(call_id, None)
        player = _make_report_player(self._pj, self, call_id, hangup_on_eof=hangup_on_eof)
        player.createPlayer(wav_path)
        player._sink = call_audio
        player.startTransmit(call_audio)
        # Keep the player alive for the whole playback (see _report_players).
        self._report_players[call_id] = player
        return True

    def stop_playback(self, call_id: str) -> None:  # pragma: no cover
        """Stop an in-flight one-way playback into ``call_id`` (IVR barge-in:
        a key pressed while a prompt is still announcing cancels it).

        Releases the transmit to the call audio and destroys the player so the
        tail of the menu can't keep talking over the caller's choice. Mirrors
        the order ``onEof2`` already uses (stop the transmit before destroying
        the player), so no EOF event is fired afterwards. No-op when no player
        is active for the call.
        """
        player = self._report_players.pop(call_id, None)
        if player is None:
            return
        sink = getattr(player, "_sink", None)
        if sink is not None:
            try:
                player.stopTransmit(sink)
            except Exception:  # noqa: BLE001 - best-effort
                pass
        destroy = getattr(player, "destroyPlayer", None)
        if destroy is not None:
            try:
                destroy()
            except Exception:  # noqa: BLE001 - best-effort
                pass

    def mark_ivr(self, call_id: str) -> None:  # pragma: no cover
        # Tag an inbound call as IVR: suppress the mic bridge during the menu
        # announcement (one-way, like a report call) so there is no echo. Pin the
        # capture device to the null sink so this call never opens the real
        # microphone or bridges it to the call, even if a later route change or
        # reroute would otherwise open the configured capture device.
        call = self._calls.get(call_id)
        if call is None:
            return
        call._is_ivr = True
        try:
            self._ep.audDevManager().setCaptureDev(self._pj.PJSUA_SND_NULL_DEV)
        except Exception:  # noqa: BLE001 - best-effort; call still works one-way
            pass

    def unmark_ivr(self, call_id: str) -> None:  # pragma: no cover
        # Restore the call's two-way bridge: the configured bridge/exit digit was
        # pressed. Re-apply the user's real device route and connect the call to
        # the sound devices through the conference bridge.
        call = self._calls.get(call_id)
        if call is None:
            return
        call._is_ivr = False
        # Restore the real device route (the configured capture device, not the
        # null sink mark_ivr pinned) before bridging.
        self._apply_route()
        info = call.getInfo()
        for media in info.media:
            if (
                media.type == self._pj.PJMEDIA_TYPE_AUDIO
                and media.status == self._pj.PJSUA_CALL_MEDIA_ACTIVE
            ):
                _bridge_two_way(self, call, media.index)
                break

    def reroute(self) -> None:  # pragma: no cover
        """Re-apply the current device selection to a live call (mid-call switch)."""
        self._apply_route()

    def set_device_change_callback(self, cb: Callable[[], None]) -> None:  # pragma: no cover
        self._device_change_cb = cb

    def recover(self) -> None:  # pragma: no cover
        """Best-effort recovery after a network drop.

        For a UDP transport pjsua2 auto-restores the signaling path; the main
        thing we must guarantee is that the call's audio is re-wired to whatever
        devices are now selected (e.g. after the machine's network came back and
        a virtual sound card reappeared).
        """
        self._apply_route()
