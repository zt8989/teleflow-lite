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
    assert "SIP registered" in joined
    assert "incoming call" in joined
    assert "enumerated" in joined
    assert "device selected" in joined


def test_log_rotates_when_file_exceeds_max_bytes(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    logger = EventLogger(path=log_path, level=LogLevel.DEBUG, max_bytes=100, backup_count=2)

    # Each line is >20 bytes, so a handful of writes should force rotation.
    for i in range(20):
        logger.info("SIP", f"line {i:03d} " + "x" * 40)

    # Current file exists and holds only a bounded tail (old lines rotated away).
    assert log_path.exists()
    current = log_path.read_text(encoding="utf-8")
    assert "line 019" in current
    assert "line 000" not in current  # rotated out of the live file

    # Backups were created and the count is capped at backup_count.
    backups = sorted(p.name for p in tmp_path.glob("app.log.*"))
    assert backups, "expected at least one rotated backup"
    assert len(backups) <= 2
    # Total retained on disk stays bounded: the live tail plus <=2 backups, none
    # of which is the whole 20-line history.
    total_lines = current.count("\n") + sum(
        (tmp_path / b).read_text(encoding="utf-8").count("\n") for b in backups
    )
    assert total_lines < 20


def test_unified_log_line_routes_same_content_to_file_and_sink(tmp_path: Path) -> None:
    # The unified API: service/hook/TTS style "[CAT] message" lines go through
    # log_line so the UI panel (sink) and the log file record the same lines.
    sink: list[str] = []
    logger = EventLogger(path=tmp_path / "app.log", sink=sink.append)
    logger.log_line("[IVR] 启动菜单: call=0")
    logger.log_line("[HOOK][ERROR] 命令执行失败: boom")

    file_lines = (tmp_path / "app.log").read_text(encoding="utf-8").splitlines()
    assert sink == file_lines
    assert "INFO [IVR] 启动菜单: call=0" in file_lines[0]
    # Nested "[ERROR]" is honored as the level, not duplicated in the message.
    assert "ERROR [HOOK] 命令执行失败: boom" in file_lines[1]


def test_unified_log_line_defaults_to_info_and_nested_warning(tmp_path: Path) -> None:
    sink: list[str] = []
    logger = EventLogger(path=tmp_path / "app.log", sink=sink.append)
    logger.log_line("[SIP] 自动选择本地端口 5062")            # no level marker -> INFO
    logger.log_line("[HOOK][WARN] 命令慢")                    # nested [WARN] -> WARNING
    logger.log_line("没有方括号的裸行")                        # fallback category APP

    file_lines = (tmp_path / "app.log").read_text(encoding="utf-8").splitlines()
    assert sink == file_lines
    assert "INFO [SIP] 自动选择本地端口 5062" in file_lines[0]
    assert "WARNING [HOOK] 命令慢" in file_lines[1]
    assert "INFO [APP] 没有方括号的裸行" in file_lines[2]


def test_console_output_mirrors_file_when_enabled(tmp_path: Path, capsys: object) -> None:
    # console=True is an optional terminal mirror; when enabled it prints the
    # exact same line the file records.
    logger = EventLogger(path=tmp_path / "app.log", console=True)
    logger.info("CALL", "call connected: 0")
    logger.log_line("[IVR] 收到按键 1")

    out = capsys.readouterr().out.strip().splitlines()  # type: ignore[attr-defined]
    file_lines = (tmp_path / "app.log").read_text(encoding="utf-8").strip().splitlines()
    assert out == file_lines
