"""Smoke test for the PyQt6 app shell (tickets 01–03).

Runs only when PyQt6 is importable and Qt is set to the offscreen platform, so
it is safe to collect in environments without a display or without PyQt6
installed. Exercises the window, the device dropdowns, the preset buttons, and
the SIP-status wiring against the fake backends.
"""

import pytest

try:
    from PyQt6.QtWidgets import QApplication  # noqa: F401

    from teleflow.app import MainWindow
    from teleflow.audio import AudioDeviceManager, FakeAudioBackend
    from teleflow.config import ConfigStore
    from teleflow.logging import EventLogger, LogLevel, attach
    from teleflow.sip import FakeSipBackend, SipCoreService

    _HAVE_GUI = True
except Exception:  # pragma: no cover - environment dependent
    _HAVE_GUI = False

pytestmark = pytest.mark.skipif(not _HAVE_GUI, reason="PyQt6 not available")


class _CloseEvent:
    """Minimal stand-in for a Qt closeEvent carrying ignore()/accept()."""

    def __init__(self) -> None:
        self.ignored = False
        self.accepted = False

    def ignore(self) -> None:
        self.ignored = True

    def accept(self) -> None:
        self.accepted = True


def _make_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    manager = AudioDeviceManager(FakeAudioBackend(), store)
    service = SipCoreService(FakeSipBackend(), store)
    window = MainWindow(manager, service)
    return app, window, service


def test_window_populates_device_comboboxes(tmp_path) -> None:
    app, window, _ = _make_window(tmp_path)
    playback_names = [
        window.settings_page.playback_cb.itemText(i)
        for i in range(window.settings_page.playback_cb.count())
    ]
    assert "VB-Cable" in playback_names
    assert window.settings_page.capture_cb.count() > 0
    window.close()


def test_debug_preset_button_selects_headset(tmp_path) -> None:
    app, window, _ = _make_window(tmp_path)
    window.settings_page.debug_btn.click()
    manager = window.settings_page._manager
    assert manager.current_selection() == ("hw:0,0", "hw:0,0")
    window.close()


def test_status_panel_reflects_sip_events(tmp_path) -> None:
    app, window, service = _make_window(tmp_path)
    sip = service._backend  # the scripted fake gateway

    service.start()
    sip.receive_register("sip:ata@192.168.1.50:5060")
    assert window.status_panel.registration.text() == "sip:ata@192.168.1.50:5060"

    sip.receive_invite("call-1")
    assert window.status_panel.call_state.text() == "通话中"

    sip.receive_bye("call-1")
    assert window.status_panel.call_state.text() == "空闲"
    window.close()


def test_log_view_tab_exists_and_appends(tmp_path) -> None:
    app, window, _ = _make_window(tmp_path)
    window.append_log_line("[INFO] hello")
    assert "[INFO] hello" in window.log_view.toPlainText()
    window.close()


def test_sip_events_appear_in_log_view(tmp_path) -> None:
    app, window, service = _make_window(tmp_path)
    logger = EventLogger(level=LogLevel.INFO, sink=window.append_log_line)
    attach(logger, service, window.settings_page._manager)

    service.start()
    service._backend.receive_register("sip:ata@192.168.1.50:5060")
    assert "gateway registered" in window.log_view.toPlainText()
    window.close()


def test_close_to_tray_hides_window(tmp_path) -> None:
    app, window, _ = _make_window(tmp_path)
    window._tray = object()  # pretend a real tray is present
    event = _CloseEvent()
    window.closeEvent(event)
    assert event.ignored is True
    assert window.isHidden() is True
    window.close()


def test_close_without_tray_quits(tmp_path) -> None:
    app, window, _ = _make_window(tmp_path)
    window._tray = None
    event = _CloseEvent()
    window.closeEvent(event)
    assert event.accepted is True
    window.close()


def test_start_minimized_hides_window(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    settings.start_minimized = True
    store.save(settings)
    manager = AudioDeviceManager(FakeAudioBackend(), store)
    service = SipCoreService(FakeSipBackend(), store)
    window = MainWindow(manager, service)
    if store.load().start_minimized:
        window.hide()
    assert window.isHidden() is True
    window.close()
