"""TDD tests for the SIP Core Service (ticket 03).

Written red (the module does not exist yet), then made green by
``src/teleflow/sip.py``. The service is driven through its public interface
against a ``FakeSipBackend`` that plays the role of the scripted ATA gateway,
so no real network or pjsua2 is required — this is the pre-agreed "scripted SIP
peer" testing seam from the spec.
"""

from teleflow.config import ConfigStore
from teleflow.sip import (
    CallState,
    EVENT_CALL_CONNECTED,
    EVENT_CALL_ENDED,
    EVENT_CALL_INCOMING,
    EVENT_GATEWAY_REGISTERED,
    EVENT_MEDIA_ERROR,
    FakeSipBackend,
    SipCoreService,
)


def _service(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    backend = FakeSipBackend()
    return SipCoreService(backend, store), backend


def test_register_emits_event_and_stores_contact(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    received = []
    svc.on(EVENT_GATEWAY_REGISTERED, lambda contact: received.append(contact))
    svc.start()
    backend.receive_register("sip:ata@192.168.1.50:5060")
    assert received == ["sip:ata@192.168.1.50:5060"]
    assert svc.registered_contact == "sip:ata@192.168.1.50:5060"


def test_incoming_invite_is_auto_answered_and_connected(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    svc.start()
    backend.receive_register("sip:ata@192.168.1.50:5060")
    incoming, connected = [], []
    svc.on(EVENT_CALL_INCOMING, lambda call_id: incoming.append(call_id))
    svc.on(EVENT_CALL_CONNECTED, lambda call_id: connected.append(call_id))

    backend.receive_invite("call-1")

    assert incoming == ["call-1"]
    assert connected == ["call-1"]
    assert svc.call_state is CallState.CONNECTED
    assert backend.answered == ["call-1"]


def test_bye_resets_state_to_idle(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    svc.start()
    backend.receive_register("sip:ata@192.168.1.50:5060")
    backend.receive_invite("call-1")
    ended = []
    svc.on(EVENT_CALL_ENDED, lambda call_id: ended.append(call_id))

    backend.receive_bye("call-1")

    assert ended == ["call-1"]
    assert svc.call_state is CallState.IDLE


def test_media_error_is_forwarded(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    svc.start()
    errors = []
    svc.on(EVENT_MEDIA_ERROR, lambda message: errors.append(message))
    backend.receive_media_error("RTP timeout")
    assert errors == ["RTP timeout"]


def test_service_starts_on_configured_port(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    settings.sip_port = 5070
    store.save(settings)

    backend = FakeSipBackend()
    svc = SipCoreService(backend, store)
    svc.start()

    assert backend.port == 5070
