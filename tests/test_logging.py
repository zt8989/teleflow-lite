"""TDD tests for the Logging subsystem (ticket 05).

Written red (teleflow.logging does not exist yet), then made green by
``src/teleflow/logging.py``. The logger is pure Python (no Qt dependency) so it
is tested with an in-memory sink and a temp file; the ``attach`` helper proves
SIP/media/device events flow into the log.
"""

from pathlib import Path

from teleflow.audio import AudioDeviceManager, FakeAudioBackend
from teleflow.config import ConfigStore
from teleflow.logging import EventLogger, LogLevel, attach
from teleflow.sip import FakeSipBackend, SipCoreService


def test_log_writes_to_file_and_sink(tmp_path: Path) -> None:
    sink: list[str] = []
    logger = EventLogger(path=tmp_path / "app.log", level=LogLevel.INFO, sink=sink.append)
    logger.info("SIP", "service started")

    assert any("service started" in line for line in sink)
    assert "service started" in (tmp_path / "app.log").read_text(encoding="utf-8")


def test_log_level_filters_below_threshold(tmp_path: Path) -> None:
    sink: list[str] = []
    logger = EventLogger(path=tmp_path / "app.log", level=LogLevel.WARNING, sink=sink.append)
    logger.info("SIP", "low")
    logger.warning("SIP", "high")

    assert len(sink) == 1
    assert "high" in sink[0]
    text = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "low" not in text and "high" in text


def test_sip_and_device_events_are_logged(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "c.json")
    sink: list[str] = []
    logger = EventLogger(path=tmp_path / "app.log", level=LogLevel.INFO, sink=sink.append)

    manager = AudioDeviceManager(FakeAudioBackend(), store)
    service = SipCoreService(FakeSipBackend(), store)
    attach(logger, service, manager)

    service.start()
    service._backend.receive_register("sip:ata@192.168.1.50:5060")
    service._backend.receive_invite("call-1")
    service._backend.receive_bye("call-1")
    manager.refresh()
    manager.set_selection("vb-cable", "blackhole")

    joined = "\n".join(sink)
    assert "gateway registered" in joined
    assert "incoming call" in joined
    assert "enumerated" in joined
    assert "device selected" in joined
