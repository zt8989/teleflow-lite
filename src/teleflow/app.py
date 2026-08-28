"""PyQt6 application shell for TeleFlow (tickets 01–03).

Owns only the UI surface: a status panel and a settings page, wired to the
Config Store, the Audio Device Manager, and the SIP Core Service. No SIP or
audio I/O happens here — those arrive in later tickets. All collaborators are
injected, so the window is testable without a display, real hardware, or pjsua2.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QMainWindow,
    QSpinBox,
    QTabWidget,
    QWidget,
)

from teleflow.audio import AudioBackend, AudioDeviceManager, FakeAudioBackend, PortAudioBackend
from teleflow.config import ConfigStore, Settings
from teleflow.sip import (
    CallState,
    EVENT_CALL_CONNECTED,
    EVENT_CALL_ENDED,
    EVENT_CALL_INCOMING,
    EVENT_GATEWAY_REGISTERED,
    EVENT_SIP_STARTED,
    EVENT_SIP_STOPPED,
    FakeSipBackend,
    SipCoreService,
)


class StatusPanel(QWidget):
    """Live view of SIP service, gateway registration, and call state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QFormLayout(self)
        self.sip_status = QLabel("未启动")
        self.registration = QLabel("未注册")
        self.call_state = QLabel("空闲")
        self.playback = QLabel("—")
        self.capture = QLabel("—")
        self.start_btn = QPushButton("启动 SIP 服务")

        layout.addRow("SIP 服务", self.sip_status)
        layout.addRow("ATA 注册", self.registration)
        layout.addRow("通话状态", self.call_state)
        layout.addRow("播放设备", self.playback)
        layout.addRow("采集设备", self.capture)
        layout.addRow(self.start_btn)

    def set_sip_status(self, text: str) -> None:
        self.sip_status.setText(text)

    def set_registration(self, text: str) -> None:
        self.registration.setText(text)

    def set_call_state(self, text: str) -> None:
        self.call_state.setText(text)


class SettingsPage(QWidget):
    """Editable settings bound to the Config Store and Audio Device Manager."""

    def __init__(self, manager: AudioDeviceManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._store = manager.store
        layout = QFormLayout(self)

        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.playback_cb = QComboBox()
        self.capture_cb = QComboBox()
        self.refresh_btn = QPushButton("刷新音频设备")
        self.autostart = QCheckBox("开机自启")
        self.start_minimized = QCheckBox("启动最小化到托盘")
        self.log_level = QComboBox()
        self.log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.debug_btn = QPushButton("调试模式")
        self.production_btn = QPushButton("生产模式")

        layout.addRow("SIP 监听端口", self.port)
        layout.addRow("播放设备", self.playback_cb)
        layout.addRow("采集设备", self.capture_cb)
        layout.addRow(self.refresh_btn)
        layout.addRow(self.autostart)
        layout.addRow(self.start_minimized)
        layout.addRow("日志等级", self.log_level)
        layout.addRow(self.debug_btn, self.production_btn)

        self.refresh_btn.clicked.connect(self._on_refresh)
        self.debug_btn.clicked.connect(lambda: self._on_preset("debug"))
        self.production_btn.clicked.connect(lambda: self._on_preset("production"))
        self.playback_cb.currentIndexChanged.connect(self._on_device_change)
        self.capture_cb.currentIndexChanged.connect(self._on_device_change)

        self.apply_settings(self._store.load())

    def _populate_devices(self) -> None:
        playback_id, capture_id = self._manager.current_selection()
        self.playback_cb.blockSignals(True)
        self.capture_cb.blockSignals(True)
        self.playback_cb.clear()
        for device in self._manager.playback_devices():
            self.playback_cb.addItem(device.name, device.id)
        self.capture_cb.clear()
        for device in self._manager.capture_devices():
            self.capture_cb.addItem(device.name, device.id)
        self._select(self.playback_cb, playback_id)
        self._select(self.capture_cb, capture_id)
        self.playback_cb.blockSignals(False)
        self.capture_cb.blockSignals(False)

    @staticmethod
    def _select(combo: QComboBox, device_id: str) -> None:
        index = combo.findData(device_id)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _on_refresh(self) -> None:
        self._manager.refresh()
        self._populate_devices()

    def _on_preset(self, preset: str) -> None:
        try:
            self._manager.apply_preset(preset)
        except ValueError:
            return
        self._populate_devices()

    def _on_device_change(self) -> None:
        playback_id = self.playback_cb.currentData()
        capture_id = self.capture_cb.currentData()
        if playback_id is None or capture_id is None:
            return
        try:
            self._manager.set_selection(playback_id, capture_id)
        except ValueError:
            self._populate_devices()

    def apply_settings(self, settings: Settings) -> None:
        self.port.setValue(settings.sip_port)
        self.autostart.setChecked(settings.autostart)
        self.start_minimized.setChecked(settings.start_minimized)
        self.log_level.setCurrentText(settings.log_level)
        self._populate_devices()

    def collect(self) -> Settings:
        playback_id = self.playback_cb.currentData()
        capture_id = self.capture_cb.currentData()
        return Settings(
            sip_port=self.port.value(),
            playback_device_id=playback_id if playback_id is not None else "",
            capture_device_id=capture_id if capture_id is not None else "",
            autostart=self.autostart.isChecked(),
            start_minimized=self.start_minimized.isChecked(),
            log_level=self.log_level.currentText(),
        )

    def save(self) -> None:
        self._store.save(self.collect())


class MainWindow(QMainWindow):
    def __init__(
        self,
        manager: AudioDeviceManager,
        service: SipCoreService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._service = service
        self.setWindowTitle("TeleFlow — 座机声音流转助手")
        self.resize(460, 400)

        tabs = QTabWidget(self)
        self.status_panel = StatusPanel()
        self.settings_page = SettingsPage(manager)
        tabs.addTab(self.status_panel, "状态")
        tabs.addTab(self.settings_page, "设置")
        self.setCentralWidget(tabs)

        self._wire_service()

    def _wire_service(self) -> None:
        svc = self._service
        svc.on(EVENT_SIP_STARTED, lambda: self._sync_sip_button())
        svc.on(EVENT_SIP_STOPPED, lambda: self._sync_sip_button())
        svc.on(EVENT_GATEWAY_REGISTERED, lambda contact: self.status_panel.set_registration(contact))
        svc.on(EVENT_CALL_INCOMING, lambda call_id: self.status_panel.set_call_state("呼入"))
        svc.on(EVENT_CALL_CONNECTED, lambda call_id: self.status_panel.set_call_state("通话中"))
        svc.on(EVENT_CALL_ENDED, lambda call_id: self.status_panel.set_call_state("空闲"))
        self.status_panel.start_btn.clicked.connect(self._toggle_sip)
        self._sync_sip_button()

    def _toggle_sip(self) -> None:
        if self._service.running:
            self._service.stop()
        else:
            self._service.start()
        self._sync_sip_button()

    def _sync_sip_button(self) -> None:
        self.status_panel.start_btn.setText(
            "停止 SIP 服务" if self._service.running else "启动 SIP 服务"
        )
        self.status_panel.set_sip_status("运行中" if self._service.running else "未启动")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.settings_page.save()
        super().closeEvent(event)


def _default_audio_backend() -> AudioBackend:
    try:
        return PortAudioBackend()
    except RuntimeError:
        warnings.warn(
            "pjsua2 unavailable; falling back to fake audio backend",
            stacklevel=2,
        )
        return FakeAudioBackend()


def _default_sip_backend() -> FakeSipBackend:
    # The real pjsua2 transport is a pending native dependency (the pjsua2 sdist
    # fails to build in this environment). Until it lands, drive the service with
    # the scripted fake backend so the app is runnable and testable headless.
    return FakeSipBackend()


def build_app(
    config_path: Path | None = None,
    audio_backend: AudioBackend | None = None,
    sip_backend: FakeSipBackend | None = None,
) -> QApplication:
    app = QApplication([])
    store = ConfigStore(config_path)
    audio_backend = audio_backend or _default_audio_backend()
    sip_backend = sip_backend or _default_sip_backend()
    manager = AudioDeviceManager(audio_backend, store)
    service = SipCoreService(sip_backend, store)
    window = MainWindow(manager, service)
    window.show()
    return app


def main() -> None:
    build_app().exec()


if __name__ == "__main__":
    main()
