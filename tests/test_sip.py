"""TDD tests for the SIP Core Service (ticket 03/04/06).

Driven through its public interface against a ``FakeSipBackend`` that plays the
role of the scripted SIP peer, so no real network or pjsua2 is required — this
is the pre-agreed "scripted SIP peer" testing seam from the spec. In the
``sip-softphone`` design the backend simulates the client-registration outcomes
that pjsua2 reports after registering to an external server.
"""

import pytest

from teleflow.config import ConfigStore
from teleflow.sip import (
    CallState,
    EVENT_CALL_CONNECTED,
    EVENT_CALL_ENDED,
    EVENT_CALL_INCOMING,
    EVENT_MEDIA_ERROR,
    EVENT_SIP_PORT_CONFLICT,
    EVENT_SIP_REGISTERED,
    EVENT_SIP_REGISTER_FAILED,
    EVENT_SIP_STARTED,
    FakeSipBackend,
    SipCoreService,
    _udp_port_available,
)


@pytest.fixture(autouse=True)
def _free_udp_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the port auto-detection deterministic: pretend every UDP port is
    free so ``start()`` always picks the default unless a test overrides the
    probe. Without this the real socket probe would depend on the host."""
    monkeypatch.setattr("teleflow.sip._udp_port_available", lambda port: True)


def _service(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    backend = FakeSipBackend()
    return SipCoreService(backend, store), backend


def test_register_emits_event_and_stores_contact(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    received = []
    svc.on(EVENT_SIP_REGISTERED, lambda contact: received.append(contact))
    svc.start()
    backend.receive_register("sip:ata@192.168.1.50:5060")
    assert received == ["sip:ata@192.168.1.50:5060"]
    assert svc.registered_contact == "sip:ata@192.168.1.50:5060"
    assert svc.is_registered


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
    svc.on(EVENT_CALL_ENDED, lambda call_id, last_digit="": ended.append(call_id))

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


def test_service_starts_on_configured_port(tmp_path, monkeypatch) -> None:
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    settings.sip_port = "5070"
    store.save(settings)

    backend = FakeSipBackend()
    svc = SipCoreService(backend, store)
    svc.start()

    assert backend.port == 5070


def test_auto_detect_skips_occupied_5060(tmp_path, monkeypatch) -> None:
    """Empty config: the resolver probes from 5060 and drifts to the first free
    port (here: 5061), exactly the co-located-registrar scenario."""
    probed: list[int] = []
    monkeypatch.setattr(
        "teleflow.sip._udp_port_available",
        lambda port: probed.append(port) or port >= 5061,
    )
    store = ConfigStore(tmp_path / "config.json")
    store.save(store.load())  # empty config file on disk

    backend = FakeSipBackend()
    svc = SipCoreService(backend, store)
    svc.start()

    assert backend.port == 5061
    assert probed[:2] == [5060, 5061]


def test_configured_occupied_port_emits_conflict_and_falls_back(
    tmp_path, monkeypatch
) -> None:
    """A configured port that is taken must warn the user and still start on
    the next free port."""
    monkeypatch.setattr("teleflow.sip._udp_port_available", lambda port: port == 5061)
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    settings.sip_port = "5060"
    store.save(settings)

    backend = FakeSipBackend()
    svc = SipCoreService(backend, store)
    conflicts: list[tuple[int, int]] = []
    svc.on(
        EVENT_SIP_PORT_CONFLICT,
        lambda requested, selected: conflicts.append((requested, selected)),
    )

    svc.start()

    assert backend.port == 5061
    assert conflicts == [(5060, 5061)]
    assert svc.running


def test_configured_free_port_does_not_emit_conflict(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("teleflow.sip._udp_port_available", lambda port: True)
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    settings.sip_port = "5070"
    store.save(settings)

    backend = FakeSipBackend()
    svc = SipCoreService(backend, store)
    conflicts: list[tuple[int, int]] = []
    svc.on(
        EVENT_SIP_PORT_CONFLICT,
        lambda requested, selected: conflicts.append((requested, selected)),
    )

    svc.start()

    assert backend.port == 5070
    assert conflicts == []


def test_udp_port_available_detects_occupied_and_released_port() -> None:
    """The real probe must see a held port as occupied and a released one as
    free (regression: a plain wildcard bind used to coexist with a concrete
    SO_REUSEADDR bind on Windows, missing the co-located registrar)."""
    import socket as socket_mod

    holder = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_DGRAM)
    holder.bind(("0.0.0.0", 0))
    port = holder.getsockname()[1]
    try:
        assert _udp_port_available(port) is False
    finally:
        holder.close()
    assert _udp_port_available(port) is True


def test_reroute_if_connected_only_fires_while_a_call_is_active(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    svc.start()
    backend.receive_register("sip:ata@192.168.1.50:5060")

    # Idle: a hotplug must not re-route (no call to re-wire).
    svc.reroute_if_connected()
    assert backend.rerouted == []

    backend.receive_invite("call-1")
    svc.reroute_if_connected()
    assert backend.rerouted == ["reroute"]

    # After the call ends we are idle again: no re-route.
    backend.receive_bye("call-1")
    svc.reroute_if_connected()
    assert backend.rerouted == ["reroute"]


def test_network_down_triggers_recovery(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    svc.start()

    backend.receive_network_down()

    assert backend.recovered == ["recovered"]


def test_recover_re_emits_sip_started(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    started: list[bool] = []
    svc.on(EVENT_SIP_STARTED, lambda: started.append(True))
    svc.start()  # initial start emits once

    backend.receive_network_down()  # recovery re-announces that the service is up

    assert len(started) == 2


def test_device_change_callback_is_invoked(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    fired: list[bool] = []
    backend.set_device_change_callback(lambda: fired.append(True))

    backend.receive_device_change()

    assert fired == [True]


def test_fake_place_report_call_is_recorded(tmp_path) -> None:
    backend = FakeSipBackend()
    backend.start(5060, lambda name, data: None)
    backend.place_report_call("sip:8000@192.168.1.116", "/tmp/r.wav")
    assert backend.report_calls == [("sip:8000@192.168.1.116", "/tmp/r.wav")]


def test_fake_report_lifecycle_fires_handler(tmp_path) -> None:
    backend = FakeSipBackend()
    events: list[tuple[str, dict]] = []
    backend.start(5060, lambda name, data: events.append((name, data)))

    backend.receive_report_connected("report-1")
    backend.play_file_to_call("report-1", "/tmp/r.wav")
    backend.receive_report_playback_done("report-1")

    assert ("report_connected", {"call_id": "report-1"}) in events
    assert backend.report_played == [("report-1", "/tmp/r.wav")]
    assert ("report_eof", {"call_id": "report-1"}) in events


# --- sip-softphone client registration path (ticket 04 / 06) ---


def test_register_without_contact_is_registered(tmp_path) -> None:
    """The client registration event may carry no Contact (scripted fake); it
    still marks the service as registered."""
    svc, backend = _service(tmp_path)
    received: list[str] = []
    svc.on(EVENT_SIP_REGISTERED, lambda contact: received.append(contact))
    svc.start()
    backend.receive_register()
    assert svc.is_registered
    assert svc.registered_contact is None
    assert received == [""]


def test_unregister_clears_registration(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    svc.start()
    backend.receive_register("sip:2001@provider.example.com")
    assert svc.is_registered
    backend.receive_unregister()
    assert not svc.is_registered
    assert svc.registered_contact is None


def test_register_failed_clears_registration_and_reports(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    failed: list[tuple[int, str]] = []
    svc.on(
        EVENT_SIP_REGISTER_FAILED,
        lambda code, reason: failed.append((code, reason)),
    )
    svc.start()
    backend.receive_register_failed(code=401, reason="Unauthorized")
    assert not svc.is_registered
    assert failed == [(401, "Unauthorized")]


def test_place_call_requires_registration(tmp_path) -> None:
    svc, backend = _service(tmp_path)
    svc.start()
    with pytest.raises(RuntimeError):
        svc.place_call("sip:2001@provider.example.com")
    backend.receive_register("sip:2001@provider.example.com")
    svc.place_call("sip:2001@provider.example.com")
    assert backend.placed == ["sip:2001@provider.example.com"]


