"""PyQt6 application shell for TeleFlow (ticket 01).

This module owns only the UI surface: a status panel and a settings page, wired
to the Config Store. It contains no SIP or audio logic — those arrive in later
tickets. The Config Store is injected so the window is testable without touching
the real user config directory.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QSpinBox,
    QTabWidget,
    QWidget,
)

from teleflow.config import ConfigStore, Settings


class StatusPanel(QWidget):
    """Read-only view of live SIP / device / call state (placeholder until
    ticket 03+ feeds it real events)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QFormLayout(self)
        self.sip_status = QLabel("未启动")
        self.registration = QLabel("未注册")
        self.call_state = QLabel("空闲")
        self.playback = QLabel("—")
        self.capture = QLabel("—")
        layout.addRow("SIP 服务", self.sip_status)
        layout.addRow("ATA 注册", self.registration)
        layout.addRow("通话状态", self.call_state)
        layout.addRow("播放设备", self.playback)
        layout.addRow("采集设备", self.capture)


class SettingsPage(QWidget):
    """Editable settings bound to the Config Store. Persists on save/close."""

    def __init__(self, store: ConfigStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        layout = QFormLayout(self)

        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.playback_id = QLineEdit()
        self.capture_id = QLineEdit()
        self.autostart = QCheckBox("开机自启")
        self.start_minimized = QCheckBox("启动最小化到托盘")
        self.log_level = QComboBox()
        self.log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])

        layout.addRow("SIP 监听端口", self.port)
        layout.addRow("播放设备 ID", self.playback_id)
        layout.addRow("采集设备 ID", self.capture_id)
        layout.addRow(self.autostart)
        layout.addRow(self.start_minimized)
        layout.addRow("日志等级", self.log_level)

        self.apply_settings(store.load())

    def apply_settings(self, settings: Settings) -> None:
        self.port.setValue(settings.sip_port)
        self.playback_id.setText(settings.playback_device_id)
        self.capture_id.setText(settings.capture_device_id)
        self.autostart.setChecked(settings.autostart)
        self.start_minimized.setChecked(settings.start_minimized)
        self.log_level.setCurrentText(settings.log_level)

    def collect(self) -> Settings:
        return Settings(
            sip_port=self.port.value(),
            playback_device_id=self.playback_id.text(),
            capture_device_id=self.capture_id.text(),
            autostart=self.autostart.isChecked(),
            start_minimized=self.start_minimized.isChecked(),
            log_level=self.log_level.currentText(),
        )

    def save(self) -> None:
        self._store.save(self.collect())


class MainWindow(QMainWindow):
    def __init__(self, store: ConfigStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self.setWindowTitle("TeleFlow — 座机声音流转助手")
        self.resize(440, 340)

        tabs = QTabWidget(self)
        self.status_panel = StatusPanel()
        self.settings_page = SettingsPage(store)
        tabs.addTab(self.status_panel, "状态")
        tabs.addTab(self.settings_page, "设置")
        self.setCentralWidget(tabs)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.settings_page.save()
        super().closeEvent(event)


def build_app(config_path: Path | None = None) -> QApplication:
    app = QApplication([])
    store = ConfigStore(config_path)
    window = MainWindow(store)
    window.show()
    return app


def main() -> None:
    build_app().exec()


if __name__ == "__main__":
    main()
