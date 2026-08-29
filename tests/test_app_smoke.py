"""Smoke test for the PyQt6 app shell (tickets 01–03).

Runs only when PyQt6 is importable and Qt is set to the offscreen platform, so
it is safe to collect in environments without a display or without PyQt6
installed. Exercises the window, the device dropdowns, the preset buttons, and
the SIP-status wiring against the fake backends.
"""

import pytest

try:
    from PyQt6.QtWidgets import QApplication, QLabel  # noqa: F401

    from teleflow.app import MainWindow, SettingsDialog, maybe_auto_start_sip
    from teleflow.audio import AudioDeviceManager, FakeAudioBackend
    from teleflow.config import ConfigStore
    from teleflow.logging import EventLogger, LogLevel, attach
    from teleflow.sip import (
        FakeSipBackend,
        SipCoreService,
    )

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
    window = MainWindow(manager, service, store)
    return app, window, service, manager, store


def test_window_populates_device_comboboxes(tmp_path) -> None:
    app, window, _, _, _ = _make_window(tmp_path)
    dash = window.dashboard
    playback_names = [
        dash._playback_cb.itemText(i)
        for i in range(dash._playback_cb.count())
    ]
    assert any("VB-Cable" in name for name in playback_names)
    assert dash._capture_cb.count() > 0
    window.close()


def test_debug_preset_button_selects_headset(tmp_path) -> None:
    app, window, _, manager, _ = _make_window(tmp_path)
    window.dashboard._debug_btn.click()
    assert manager.current_selection() == ("hw:0,0", "hw:0,0")
    window.close()


def test_status_panel_reflects_sip_events(tmp_path) -> None:
    app, window, service, _, _ = _make_window(tmp_path)
    sip = service._backend  # the scripted fake gateway
    dash = window.dashboard

    service.start()
    sip.receive_invite("call-1")
    assert dash._call_state.value == "connected"

    sip.receive_bye("call-1")
    assert dash._call_state.value == "ended"
    window.close()


def test_log_view_tab_exists_and_appends(tmp_path) -> None:
    app, window, _, _, _ = _make_window(tmp_path)
    window.append_log_line("[INFO] hello")
    assert "[INFO] hello" in window.dashboard._log_view.toPlainText()
    window.close()


def test_sip_events_appear_in_log_view(tmp_path) -> None:
    app, window, service, manager, _ = _make_window(tmp_path)
    logger = EventLogger(level=LogLevel.INFO, sink=window.append_log_line)
    attach(logger, service, manager)

    service.start()
    service._backend.receive_register("sip:ata@192.168.1.50:5060")
    assert "SIP registered" in window.dashboard._log_view.toPlainText()
    window.close()


def test_close_to_tray_hides_window(tmp_path) -> None:
    app, window, _, _, _ = _make_window(tmp_path)
    window._tray = object()  # pretend a real tray is present
    event = _CloseEvent()
    window.closeEvent(event)
    assert event.ignored is True
    assert window.isHidden() is True
    window.close()


def test_close_without_tray_quits(tmp_path) -> None:
    app, window, _, _, _ = _make_window(tmp_path)
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
    window = MainWindow(manager, service, store)
    if store.load().start_minimized:
        window.hide()
    assert window.isHidden() is True
    window.close()


def test_sip_registration_updates_dashboard_card(tmp_path) -> None:
    app, window, service, _, _ = _make_window(tmp_path)
    dash = window.dashboard
    sip = service._backend
    service.start()

    sip.receive_register("sip:2001@provider.example.com")
    assert "已注册" in dash._reg_stat.findChild(QLabel, "stat_value").text()

    sip.receive_unregister()
    assert "未注册" in dash._reg_stat.findChild(QLabel, "stat_value").text()

    sip.receive_register_failed(code=403, reason="Forbidden")
    assert "注册失败" in dash._reg_stat.findChild(QLabel, "stat_value").text()
    window.close()


def test_settings_dialog_round_trips_sip_account(tmp_path) -> None:
    app, window, service, manager, _ = _make_window(tmp_path)
    dialog = SettingsDialog(manager, window)
    dialog.sip_host.setText("192.168.1.189")
    dialog.sip_server_port.setValue(5062)
    dialog.sip_user.setText("1002")
    dialog.sip_password.setText("secret")
    dialog._save_and_close()

    reloaded = ConfigStore(tmp_path / "config.json").load()
    assert reloaded.sip_host == "192.168.1.189"
    assert reloaded.sip_server_port == 5062
    assert reloaded.sip_user == "1002"
    assert reloaded.sip_password == "secret"
    window.close()


def test_settings_dialog_ivr_exit_digit_checkbox_round_trip(tmp_path) -> None:
    app, window, service, manager, _ = _make_window(tmp_path)
    dialog = SettingsDialog(manager, window)
    dialog.ivr_exit_checkboxes["0"].setChecked(True)
    dialog._save_and_close()

    reloaded = ConfigStore(tmp_path / "config.json").load()
    assert reloaded.ivr_exit_digit == "0"
    # Reload into a fresh dialog: only digit "0" is checked.
    dialog2 = SettingsDialog(manager, window)
    assert dialog2.ivr_exit_checkboxes["0"].isChecked() is True
    assert all(
        not cb.isChecked() for d, cb in dialog2.ivr_exit_checkboxes.items() if d != "0"
    )
    window.close()


def test_settings_dialog_ivr_exit_checkbox_single_selection(tmp_path) -> None:
    app, window, service, manager, _ = _make_window(tmp_path)
    dialog = SettingsDialog(manager, window)
    dialog.ivr_exit_checkboxes["1"].setChecked(True)
    dialog.ivr_exit_checkboxes["0"].setChecked(True)  # must uncheck "1"
    checked = [d for d, cb in dialog.ivr_exit_checkboxes.items() if cb.isChecked()]
    assert checked == ["0"]
    window.close()


# --- dashboard top menu shares the tray's actions (gateway-auto-connect) ---


def test_dashboard_menu_uses_same_actions_as_tray(tmp_path) -> None:
    app, window, _, _, _ = _make_window(tmp_path)
    dash = window.dashboard
    menu = dash._menu_btn.menu()
    assert menu is not None
    actions = menu.actions()
    assert [a.text() for a in actions] == [
        "启动 SIP 服务",
        "显示窗口",
        "设置",
        "测试汇报",
        "退出",
    ]
    # Same QAction instances as the tray menu -> labels/state stay in sync.
    assert actions[0] is window._act_toggle_sip
    assert actions[4] is window._act_quit
    window.close()


def test_toggle_sip_persists_auto_connect_flag(tmp_path) -> None:
    app, window, service, _, store = _make_window(tmp_path)
    store.load().sip_auto_connect = False
    store.save(store.load())

    window._toggle_sip()  # start
    assert service.running
    assert store.load().sip_auto_connect is True

    window._toggle_sip()  # stop
    assert not service.running
    assert store.load().sip_auto_connect is False
    window.close()


# --- launch-time auto-connect (maybe_auto_start_sip) ---


def test_auto_start_connects_when_config_complete(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("teleflow.sip._udp_port_available", lambda port: True)
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    settings.sip_host = "192.168.1.189"
    settings.sip_user = "1002"
    settings.sip_password = "1234"
    store.save(settings)

    service = SipCoreService(FakeSipBackend(), store)
    log = []
    started = maybe_auto_start_sip(service, store, log.append)

    assert started is True
    assert service.running
    assert store.load().sip_auto_connect is True


def test_auto_start_stays_stopped_on_incomplete_config(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    settings.sip_host = "192.168.1.189"  # no sip_user
    store.save(settings)

    service = SipCoreService(FakeSipBackend(), store)
    log = []
    started = maybe_auto_start_sip(service, store, log.append)

    assert started is False
    assert not service.running
    assert store.load().sip_auto_connect is False
    assert any("配置不完整" in line for line in log)


def test_auto_start_falls_back_to_stopped_on_startup_error(
    tmp_path, monkeypatch
) -> None:
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    settings.sip_host = "192.168.1.189"
    settings.sip_user = "1002"
    store.save(settings)

    service = SipCoreService(FakeSipBackend(), store)

    def _boom() -> None:
        raise RuntimeError("no free port")

    monkeypatch.setattr(service, "start", _boom)
    log = []
    started = maybe_auto_start_sip(service, store, log.append)

    assert started is False
    assert not service.running
    assert store.load().sip_auto_connect is False
    assert any("自动连接网关失败" in line for line in log)


def test_auto_start_respects_disabled_flag(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    settings.sip_host = "192.168.1.189"
    settings.sip_user = "1002"
    settings.sip_auto_connect = False
    store.save(settings)

    service = SipCoreService(FakeSipBackend(), store)
    started = maybe_auto_start_sip(service, store, lambda line: None)

    assert started is False
    assert not service.running

# --- async test-report handoff (GUI slot after background synthesis) ---


def test_finish_test_report_starts_report_with_wav(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("teleflow.sip._udp_port_available", lambda port: True)
    app, window, service, _, store = _make_window(tmp_path)
    settings = store.load()
    settings.report_host = "192.168.1.116"
    settings.report_extension = "8000"
    store.save(settings)
    service.start()
    wav = tmp_path / "report.wav"
    wav.write_bytes(b"RIFF")

    window._pending_report_text = "会议纪要"
    window._pending_report_mp3 = str(tmp_path / "report.mp3")
    window._pending_report_wav = str(wav)
    window._pending_report_error = None
    window._finish_test_report()

    assert service._backend.report_calls == [
        ("sip:8000@192.168.1.116:5060", str(wav))
    ]
    assert "[FFMPEG] 转码完成" in window.dashboard._log_view.toPlainText()
    window.close()


def test_finish_test_report_logs_synthesis_error(tmp_path) -> None:
    app, window, _, _, _ = _make_window(tmp_path)
    window._pending_report_error = "connection reset"
    window._finish_test_report()

    assert "测试汇报失败: connection reset" in window.dashboard._log_view.toPlainText()
    assert window._pending_report_error is None
    window.close()


# --- phone-report routing (teleflow-phone-report-routing: ticket 02) ---


def test_report_defaults_to_gateway_route_via_settings(tmp_path) -> None:
    """Only the extension is required: leaving 座机地址 empty makes a report
    dial the configured gateway (走网关), not a desk phone."""
    from teleflow.sip import SipCoreService as _Svc
    from teleflow.tts import FakeTtsBackend

    app, window, _, manager, store = _make_window(tmp_path)
    dialog = SettingsDialog(manager, window)
    dialog.sip_host.setText("192.168.1.189")
    dialog.sip_server_port.setValue(5062)
    dialog.report_extension.setText("8000")
    dialog.report_host.setText("")  # 座机地址 left empty -> default gateway route
    dialog._save_and_close()

    reloaded = ConfigStore(tmp_path / "config.json").load()
    assert reloaded.sip_host == "192.168.1.189"
    assert reloaded.sip_server_port == 5062
    assert reloaded.report_extension == "8000"
    assert reloaded.report_host == ""

    svc = _Svc(FakeSipBackend(), store, tts=FakeTtsBackend())
    svc.start()
    svc.start_report("测试汇报")
    assert svc._backend.report_calls == [
        ("sip:8000@192.168.1.189:5062", str(svc._report_wav))
    ]
    window.close()
