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

from typing import Any, Callable

from teleflow.config import ConfigStore
from teleflow.media import AudioDeviceController, MediaBridge
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
            if info.state == pj.PJSIP_INV_STATE_DISCONNECTED:
                call_id = str(info.id)
                backend._calls.pop(call_id, None)
                # A report/IVR call's player is no longer needed once the call is
                # torn down; release it so the underlying C++ object is freed.
                if getattr(self, "_is_report", False) or getattr(self, "_is_ivr", False):
                    _release_report_player(backend, call_id)
                # Tell the service the call ended so it can reset its call state;
                # otherwise the UI stays stuck on "通话中" after a hang-up. pjsua2
                # dispatches this callback to the attached Call object once the
                # call is torn down. A report call drives its own lifecycle via
                # "report_eof", so it must not emit the normal call-ended event.
                if not getattr(self, "_is_report", False) and backend._handler is not None:
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
                # Signal connect exactly once and let the service drive playback.
                if getattr(self, "_is_report", False):
                    if not getattr(self, "_report_connected_fired", False):
                        self._report_connected_fired = True
                        if backend._handler is not None:
                            backend._handler(
                                "report_connected", {"call_id": str(info.id)}
                            )
                    return
                # IVR call: one-way welcome/menu playback only, no mic/speaker
                # bridge; the service drives playback and listens for DTMF.
                if getattr(self, "_is_ivr", False):
                    return
                # Normal call: bridge the call's audio to the selected devices
                # through the conference bridge: downstream call -> playback
                # device, upstream capture device -> call. No recorder / transform.
                call_audio = self.getAudioMedia(media.index)
                dev_mgr = backend._ep.audDevManager()
                # Downstream: decoded call audio -> selected playback device.
                call_audio.startTransmit(dev_mgr.getPlaybackDevMedia())
                # Upstream: selected capture device -> call audio (to telephone).
                dev_mgr.getCaptureDevMedia().startTransmit(call_audio)

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
                backend._handler("register", {})
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
            self._ep.libInit(self._pj.EpConfig())
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
        if cap not in ("", "-1", None):
            self._bridge.apply_capture(cap)

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
            call.hangup()

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

    def play_file_to_call(self, call_id: str, wav_path: str, *, hangup_on_eof: bool = False) -> None:  # pragma: no cover
        # One-way playback: file -> call audio only. No capture device, no
        # recorder. The player subclassed below fires ``onEof2`` when the file
        # ends: for a report call that signals EOF (service hangs up); for an
        # IVR menu item it signals ``playback_done`` (service plays the next).
        call = self._calls.get(call_id)
        if call is None:
            return
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
            return
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

    def mark_ivr(self, call_id: str) -> None:  # pragma: no cover
        """Tag an inbound call as IVR so ``onCallMediaState`` skips the mic
        bridge (one-way welcome/menu playback instead of a two-way bridge)."""
        call = self._calls.get(call_id)
        if call is not None:
            call._is_ivr = True

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
