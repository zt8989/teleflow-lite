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


def _make_classes(pj: Any, backend: "Pjsua2Backend") -> tuple[type, type]:
    """Build the pjsua2 Account/Call subclasses wired to this backend."""

    class Call(pj.Call):  # type: ignore[misc, valid-type]
        def __init__(self, account: Any) -> None:
            pj.Call.__init__(self, account)

        def onCallState(self, prm: Any) -> None:  # noqa: ARG002 - prm unused
            info = self.getInfo()
            if info.state == pj.PJSIP_INV_STATE_DISCONNECTED:
                backend._calls.pop(str(info.id), None)

        def onCallMediaState(self, prm: Any) -> None:  # noqa: ARG002 - prm unused
            # Bridge the call's audio to the selected devices through the
            # conference bridge: downstream call -> playback device, upstream
            # capture device -> call. No recorder / transform is inserted.
            info = self.getInfo()
            for media in info.media:
                if (
                    media.type == pj.PJMEDIA_TYPE_AUDIO
                    and media.status == pj.PJSUA_CALL_MEDIA_ACTIVE
                ):
                    call_audio = self.getAudioMedia(media.index)
                    dev_mgr = backend._ep.audDevManager()
                    # Downstream: decoded call audio -> selected playback device.
                    call_audio.startTransmit(dev_mgr.getPlaybackDevMedia())
                    # Upstream: selected capture device -> call audio (to telephone).
                    dev_mgr.getCaptureDevMedia().startTransmit(call_audio)

    class Account(pj.Account):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            pj.Account.__init__(self)

        def onIncomingCall(self, prm: Any) -> None:
            call = Call(self)
            call_id = str(prm.callId)
            backend._calls[call_id] = call
            # Do NOT answer here: SipCoreService drives the answer via
            # backend.answer(call_id) so the same code path serves the fake
            # and the real backend.
            if backend._handler is not None:
                backend._handler("invite", {"call_id": call_id})

    return Call, Account


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
        self._lib_created = False
        self._call_cls: type | None = None
        self._account_cls: type | None = None
        self._device_change_cb: Callable[[], None] | None = None
        self.running = False
        self.port: int | None = None

        # pjsua2's Endpoint is a process-wide singleton; subclass it so we can
        # hook the audio-device-state and transport-state callbacks in one place.
        backend_ref = self

        class Endpoint(pj.Endpoint):  # type: ignore[misc, valid-type]
            def onAudioDevState(self, prm: Any) -> None:  # noqa: ARG002 - prm unused
                # A device was plugged/unplugged: let the manager re-enumerate
                # and re-route a live call.
                if backend_ref._device_change_cb is not None:
                    backend_ref._device_change_cb()

            def onTransportState(self, prm: Any) -> None:  # noqa: ARG002 - prm unused
                state = getattr(prm, "state", None)
                if state == pj.PJSIP_TP_STATE_DISCONNECTED and backend_ref._handler is not None:
                    backend_ref._handler("network_down", {})

        self._ep = Endpoint()

    def _ensure_lib(self) -> None:  # pragma: no cover
        if not self._lib_created:
            self._ep.libCreate()
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
        acc_cfg.idUri = "sip:teleflow@localhost"
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
            op = self._pj.CallOpParam()
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
        call.makeCall(target)

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
