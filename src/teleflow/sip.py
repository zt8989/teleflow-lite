"""SIP core service (ticket 03).

A local SIP UA boundary. The service talks to a ``SipBackend`` protocol so the
real pjsua2 transport and a ``FakeSipBackend`` (the scripted ATA gateway used in
tests) are interchangeable — that seam is the spec's "scripted SIP peer" testing
strategy. The service owns SIP/call *state* and translates raw backend events
into domain events the UI subscribes to; it performs no socket I/O itself.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Protocol, runtime_checkable

from teleflow.config import ConfigStore

# Domain events emitted to subscribers.
EVENT_SIP_STARTED = "sip_started"
EVENT_SIP_STOPPED = "sip_stopped"
EVENT_GATEWAY_REGISTERED = "gateway_registered"
EVENT_CALL_INCOMING = "call_incoming"
EVENT_CALL_CONNECTED = "call_connected"
EVENT_CALL_ENDED = "call_ended"
EVENT_MEDIA_ERROR = "media_error"


class CallState(str, Enum):
    IDLE = "idle"
    INCOMING = "incoming"
    CONNECTED = "connected"
    ENDED = "ended"


@runtime_checkable
class SipBackend(Protocol):
    """Low-level SIP transport. Reports raw events to the handler the service
    registers, and answers/hangs up/places calls on demand."""

    def start(self, port: int, handler: Callable[[str, dict], None]) -> None: ...
    def stop(self) -> None: ...
    def answer(self, call_id: str) -> None: ...
    def hangup(self, call_id: str) -> None: ...
    def place_call(self, target: str) -> None: ...
    def reroute(self) -> None:
        """Re-apply the current device selection to a live call (mid-call switch)."""
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

    def reroute(self) -> None:
        # Scripted fake has no live call audio to re-route.
        pass


class SipCoreService:
    """Local UA: accepts ATA REGISTER, stores the Contact, auto-answers INVITE,
    and emits call/media state as domain events. State resets cleanly on
    hang-up or abnormal disconnect.
    """

    def __init__(self, backend: SipBackend, store: ConfigStore) -> None:
        self._backend = backend
        self._store = store
        self._state = CallState.IDLE
        self._contact: str | None = None
        self._running = False
        self._subscribers: dict[str, list[Callable[..., None]]] = {}

    def on(self, event: str, callback: Callable[..., None]) -> None:
        self._subscribers.setdefault(event, []).append(callback)

    def _emit(self, event: str, **data: object) -> None:
        for callback in self._subscribers.get(event, []):
            callback(**data)

    @property
    def call_state(self) -> CallState:
        return self._state

    @property
    def registered_contact(self) -> str | None:
        return self._contact

    @property
    def running(self) -> bool:
        return self._running

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

    def reroute(self) -> None:
        """Re-apply the current device selection to a live call (mid-call switch).

        The backend (real pjsua2) re-wires the conference bridge to the freshly
        selected devices; the fake is a no-op.
        """
        self._backend.reroute()

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
        elif name == "media_error":
            self._emit(EVENT_MEDIA_ERROR, message=str(data.get("message", "")))
