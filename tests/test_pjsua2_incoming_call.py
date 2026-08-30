"""Regression test for inbound (incoming) call handling — ticket 04.

The native pjsua2 ``Call`` object is only attached to the real SIP call when it
is constructed with the incoming call id: pjsua2's ``Call::Call`` (see
pjsua2 call.cpp) sets the call's user data — and thus a usable id — only when
``call_id != PJSUA_INVALID_ID``. Building ``Call(account)`` with the default id
leaves the object detached, so ``backend.answer()`` operates on an invalid call
and the inbound call can never be answered ("无法接通").

This test models that attachment contract with a fake pjsua2 (the real native
extension is unavailable in this environment) and asserts the backend wires the
incoming call to the real id. It does not require the native library.
"""

import sys
import types

import pytest

from teleflow import pjsua2_backend
from teleflow.config import ConfigStore


def _make_fake_pj() -> types.SimpleNamespace:
    """Minimal pjsua2 stand-in modeling the Call-attach + disconnect contract."""

    PJSUA_INVALID_ID = -1
    PJSIP_INV_STATE_EARLY = 3
    # A non-disconnected state; tests override getInfo per-instance to simulate
    # a teardown.
    PJSIP_INV_STATE_CONFIRMED = 5
    PJSIP_INV_STATE_DISCONNECTED = 6
    PJMEDIA_TYPE_AUDIO = 0
    PJSUA_CALL_MEDIA_ACTIVE = 1

    class Call:
        def __init__(self, account: object, call_id: int = PJSUA_INVALID_ID) -> None:
            # Mirror pjsua2: the Call only gets a real id when call_id is valid.
            self.id = call_id
            self._state = PJSIP_INV_STATE_CONFIRMED
            self._media: list = []

        def getInfo(self) -> types.SimpleNamespace:
            return types.SimpleNamespace(
                state=self._state, id=self.id, media=list(self._media)
            )

    class Account:
        def __init__(self) -> None:
            pass

    class Endpoint:
        def __init__(self) -> None:
            pass

    return types.SimpleNamespace(
        Call=Call,
        Account=Account,
        Endpoint=Endpoint,
        PJSUA_INVALID_ID=PJSUA_INVALID_ID,
        PJSIP_INV_STATE_EARLY=PJSIP_INV_STATE_EARLY,
        PJSIP_INV_STATE_CONFIRMED=PJSIP_INV_STATE_CONFIRMED,
        PJSIP_INV_STATE_DISCONNECTED=PJSIP_INV_STATE_DISCONNECTED,
        PJMEDIA_TYPE_AUDIO=PJMEDIA_TYPE_AUDIO,
        PJSUA_CALL_MEDIA_ACTIVE=PJSUA_CALL_MEDIA_ACTIVE,
    )


@pytest.fixture
def fake_pj(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    # Keep the process-wide singleton isolated from the real-backend tests.
    pjsua2_backend._shared_endpoint = None
    pjsua2_backend._shared_backend = None
    pj = _make_fake_pj()
    monkeypatch.setitem(sys.modules, "pjsua2", pj)
    yield pj
    pjsua2_backend._shared_endpoint = None
    pjsua2_backend._shared_backend = None


def test_incoming_call_is_attached_to_real_call_id(
    tmp_path, fake_pj: types.SimpleNamespace
) -> None:
    store = ConfigStore(tmp_path / "c.json")
    backend = pjsua2_backend.Pjsua2Backend(store)
    events: list[tuple[str, dict]] = []
    backend._handler = lambda name, data: events.append((name, data))
    Call, Account = pjsua2_backend._make_classes(fake_pj, backend)
    backend._call_cls = Call

    acc = Account()
    prm = types.SimpleNamespace(callId=7)
    acc.onIncomingCall(prm)

    # The Call object created for the inbound call must carry the real call id;
    # otherwise backend.answer() targets a detached (invalid) call and the
    # caller can never be connected.
    call = backend._calls["7"]
    assert call.id == 7
    assert events == [("invite", {"call_id": "7"})]


def test_disconnect_notifies_service_and_clears_call(
    tmp_path, fake_pj: types.SimpleNamespace
) -> None:
    store = ConfigStore(tmp_path / "c.json")
    backend = pjsua2_backend.Pjsua2Backend(store)
    events: list[tuple[str, dict]] = []
    backend._handler = lambda name, data: events.append((name, data))
    Call, Account = pjsua2_backend._make_classes(fake_pj, backend)
    backend._call_cls = Call

    acc = Account()
    prm = types.SimpleNamespace(callId=7)
    acc.onIncomingCall(prm)
    call = backend._calls["7"]

    # Simulate pjsua2 tearing the call down: onCallState(DISCONNECTED).
    DISCONNECTED = fake_pj.PJSIP_INV_STATE_DISCONNECTED
    call.getInfo = lambda: types.SimpleNamespace(state=DISCONNECTED, id=7)
    call.onCallState(types.SimpleNamespace())

    # The service must learn the call ended so it leaves the "通话中" state, and
    # the call must be dropped from the backend's active set.
    assert ("bye", {"call_id": "7"}) in events
    assert "7" not in backend._calls


def _report_call(backend: pjsua2_backend.Pjsua2Backend, fake_pj, call_id: int):
    """A report Call wired to ``backend``, like place_report_call creates it."""
    Call, Account = pjsua2_backend._make_classes(fake_pj, backend)
    backend._call_cls = Call
    call = Call(Account())
    call._is_report = True
    call._report_connected_fired = False
    call._report_call_confirmed = False
    call.id = call_id
    backend._calls[str(call_id)] = call
    return call


def _active_media(fake_pj) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        type=fake_pj.PJMEDIA_TYPE_AUDIO,
        status=fake_pj.PJSUA_CALL_MEDIA_ACTIVE,
    )


def test_report_playback_starts_only_after_the_phone_answers(
    tmp_path, fake_pj: types.SimpleNamespace
) -> None:
    """Early media (183 w/ SDP) activates the stream while the phone is still
    ringing; playback must wait for the real answer (200/INVITE -> CONFIRMED),
    or the caller hears the middle of the report when they pick up."""
    store = ConfigStore(tmp_path / "c.json")
    backend = pjsua2_backend.Pjsua2Backend(store)
    events: list[tuple[str, dict]] = []
    backend._handler = lambda name, data: events.append((name, data))
    call = _report_call(backend, fake_pj, 9)

    # Ringing: media is ACTIVE via early media, call still EARLY — no playback.
    call._state = fake_pj.PJSIP_INV_STATE_EARLY
    call._media = [_active_media(fake_pj)]
    call.onCallMediaState(None)
    assert events == []

    # Phone picked up: CONFIRMED + already-active media -> connect now.
    call._state = fake_pj.PJSIP_INV_STATE_CONFIRMED
    call.onCallState(None)
    assert events == [("report_connected", {"call_id": "9"})]

    # Fires exactly once.
    call.onCallMediaState(None)
    assert events == [("report_connected", {"call_id": "9"})]


def test_report_connected_waits_for_media_after_answer(
    tmp_path, fake_pj: types.SimpleNamespace
) -> None:
    """CONFIRMED arriving before the media finished activating must not be lost:
    the connect fires as soon as the media becomes active afterwards."""
    store = ConfigStore(tmp_path / "c.json")
    backend = pjsua2_backend.Pjsua2Backend(store)
    events: list[tuple[str, dict]] = []
    backend._handler = lambda name, data: events.append((name, data))
    call = _report_call(backend, fake_pj, 10)

    call._state = fake_pj.PJSIP_INV_STATE_CONFIRMED
    call._media = []
    call.onCallState(None)
    assert events == []

    call._media = [_active_media(fake_pj)]
    call.onCallMediaState(None)
    assert events == [("report_connected", {"call_id": "10"})]
