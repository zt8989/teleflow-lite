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
    from teleflow.pjsua2_backend import Pjsua2Backend

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
