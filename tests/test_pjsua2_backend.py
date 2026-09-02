"""Tests for the real pjsua2 backend (ticket 04).

These only run where the native pjsua2 extension is built and importable. They
exercise the parts that don't need a live SIP peer or audio hardware: the
import guard, library init/teardown (a real UDP transport is created and the
endpoint started/stopped), and that starting with no device selected does not
crash on the empty device id.

NOTE: pjsua2's Endpoint is a process-wide singleton, so this module constructs
exactly one backend across its tests.
"""

import pytest

try:
    import pjsua2  # noqa: F401 - the native extension must be importable
    from teleflow.config import ConfigStore
    from teleflow.pjsua2_backend import Pjsua2Backend, _new_call_op

    _HAVE_PJSUA2 = True
except ImportError:  # pragma: no cover - environment dependent
    _HAVE_PJSUA2 = False

pytestmark = pytest.mark.skipif(not _HAVE_PJSUA2, reason="pjsua2 native lib not built")


def test_real_backend_starts_stops_and_tolerates_no_device(tmp_path) -> None:
    store = ConfigStore(tmp_path / "c.json")
    backend = Pjsua2Backend(store)
    # Default config has empty device ids; starting must not choke on int("").
    backend.start(5090, lambda name, data: None)
    assert backend.running is True
    assert backend.port == 5090
    backend.stop()
    assert backend.running is False


def test_place_call_and_place_report_call_do_not_raise(tmp_path) -> None:
    """Regression: pjsua2's Call.makeCall(dst_uri, prm) requires an explicit
    CallOpParam; the outbound paths used to call makeCall(target) and blew up
    with 'missing 1 required positional argument: prm' the moment a real call
    was placed (report flow and manual outbound calls)."""
    store = ConfigStore(tmp_path / "c.json")
    backend = Pjsua2Backend(store)
    backend.start(5090, lambda name, data: None)
    wav = tmp_path / "report.wav"
    wav.write_bytes(b"RIFF")
    try:
        backend.place_call("sip:1001@127.0.0.1:5099")
        backend.place_report_call("sip:8000@127.0.0.1:5099", str(wav))
    finally:
        backend.stop()


def test_stop_from_foreign_thread_does_not_abort(tmp_path) -> None:
    """Regression: the UI stops the SIP service on a worker thread so
    libDestroy's ~0.5s teardown doesn't freeze the GUI. An unregistered
    foreign thread used to abort inside pj_thread_this ("Calling pjlib from
    unknown/external thread") on the first pj_log of pjsua_destroy2 — before
    this fix the whole process died with SIGABRT, taking pytest with it."""
    import threading

    store = ConfigStore(tmp_path / "c.json")
    backend = Pjsua2Backend(store)
    backend.start(5092, lambda name, data: None)
    result: dict[str, BaseException | None] = {"exc": None}

    def worker() -> None:
        try:
            backend.stop()
        except BaseException as exc:  # noqa: BLE001 - capture, never propagate
            result["exc"] = exc

    t = threading.Thread(target=worker, name="sip-stop")
    t.start()
    t.join(timeout=10)
    assert not t.is_alive()
    assert result["exc"] is None
    assert backend.running is False


def test_new_call_op_requests_audio_only(tmp_path) -> None:
    """Regression: pjsua2's default call setting adds a T.140 ``m=text`` SDP
    line, which the NewRockTech ATA rejects with 415 (phone never rings).
    Calls must use an op that requests audio only — and the setting must not
    be "empty", or makeCall falls back to the default and re-adds the line.
    Also regression: outbound calls keep a reference in backend._calls so the
    wrapper is not GC'd mid-call ("Call 0 hanging up" right after makeCall)."""
    import pjsua2 as pj

    op = _new_call_op(pj)
    assert op.opt.isEmpty() is False
    assert op.opt.audioCount == 1
    assert op.opt.textCount == 0
    assert op.opt.videoCount == 0

    store = ConfigStore(tmp_path / "c.json")
    backend = Pjsua2Backend(store)
    backend.start(5091, lambda name, data: None)
    try:
        backend.place_report_call("sip:8000@127.0.0.1:5099", "C:/nope.wav")
        assert len(backend._calls) == 1  # wrapper kept alive, not GC'd
    finally:
        backend.stop()
