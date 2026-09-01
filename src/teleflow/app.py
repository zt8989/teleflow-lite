"""PyQt6 application shell for TeleFlow.

Owns the UI surface (dashboard with status cards, audio routing, live log;
system tray menu; settings modal) and wires it to the Config Store, the Audio
Device Manager, and the SIP Core Service. SIP and audio I/O are delegated to
injected backends (real pjsua2 when available, scripted fakes otherwise), so the
window is testable without a display, real hardware, or the native library.
"""

from __future__ import annotations

import shutil
import signal
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QEvent, QMetaObject, QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPixmap, QTextCharFormat
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QStackedWidget,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from teleflow.audio import (
    AudioBackend,
    AudioDeviceManager,
    DeviceKind,
    EVENT_AUDIO_DEVICES_CHANGED,
    EVENT_DEVICE_SELECTED,
    FakeAudioBackend,
    PortAudioBackend,
)
from teleflow.autostart import set_autostart
from teleflow.config import BUILTIN_TTS_VOICES, ConfigStore, Settings
from teleflow.logging import EventLogger, LogLevel, attach
from teleflow.hooks import SubprocessHookRunner, attach_hooks
from teleflow.pjsua2_backend import Pjsua2Backend
from teleflow.sip import (
    CallState,
    EVENT_CALL_CONNECTED,
    EVENT_CALL_ENDED,
    EVENT_CALL_INCOMING,
    EVENT_REPORT_COMPLETED,
    EVENT_REPORT_CONNECTED,
    EVENT_REPORT_FAILED,
    EVENT_REPORT_PLAYING,
    EVENT_REPORT_STARTED,
    EVENT_SIP_REGISTERED,
    EVENT_SIP_REGISTER_FAILED,
    EVENT_SIP_STARTED,
    EVENT_SIP_STOPPED,
    EVENT_SIP_UNREGISTERED,
    EVENT_SIP_PORT_CONFLICT,
    FakeSipBackend,
    ReportState,
    SipBackend,
    SipCoreService,
)
from teleflow.i18n import register_on_change, set_language, tr
from teleflow.rpc import RpcServer
from teleflow.tts import CachingTtsBackend, EdgeTtsBackend

# Maps the built-in TTS voice display names (config.BUILTIN_TTS_VOICES) to i18n
# keys so the voice dropdown labels follow the UI language.
_TTS_VOICE_KEYS: dict[str, str] = {
    "晓晓（女）": "tts.voice.xiaoxiao",
    "云希（男）": "tts.voice.yunxi",
    "云扬（男·新闻）": "tts.voice.yunyang",
    "晓伊（女·川渝）": "tts.voice.xiaoyi",
    "云健（男）": "tts.voice.yunjian",
    "晓辰（女）": "tts.voice.xiaochen",
    "云霞（女）": "tts.voice.yunxia",
    "云野（男）": "tts.voice.yunye",
    "Aria（en-US 女）": "tts.voice.aria",
    "Guy（en-US 男）": "tts.voice.guy",
}


# ---------------------------------------------------------------------------
# SVG icon helper
# ---------------------------------------------------------------------------
def _load_icon() -> QIcon:
    """Load the TeleFlow color SVG icon (for window/dock).

    Falls back to a green square if missing. Works both when running from source
    (``__file__`` relative) and from a PyInstaller-frozen app (``sys._MEIPASS``
    relative).
    """
    return _load_svg("teleflow-icon.svg")


def _load_tray_icon() -> QIcon:
    """Load the TeleFlow tray icon.

    Only macOS uses the monochrome SVG, set as a mask so it becomes a template
    image that adapts to light/dark menu bar appearance. Every other platform
    uses the colored icon.
    """
    if sys.platform == "darwin":
        icon = _load_svg("teleflow-icon-mono.svg")
        icon.setIsMask(True)
        return icon
    return _load_icon()


def _load_svg(name: str) -> QIcon:
    """Load an SVG icon from the prototypes directory by name."""
    candidates = []
    # 1. Running from source
    candidates.append(Path(__file__).resolve().parent.parent.parent / "prototypes" / name)
    # 2. Frozen PyInstaller bundle
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        candidates.append(Path(meipass) / "prototypes" / name)
    for icon_path in candidates:
        if icon_path.exists():
            return QIcon(str(icon_path))
    icon_pix = QPixmap(16, 16)
    icon_pix.fill(Qt.GlobalColor.darkGreen)
    return QIcon(icon_pix)


# ---------------------------------------------------------------------------
# Dashboard — single-page card-based layout (prototype layout reference)
# ---------------------------------------------------------------------------
class DashboardWidget(QWidget):
    """Single-page Dashboard: stat grid + audio routing + live log."""

    def __init__(
        self,
        manager: AudioDeviceManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._sip_running = False
        self._call_state: CallState = CallState.IDLE
        self._report_state: ReportState = ReportState.IDLE
        self._reg_status: tuple[str, int | None] = ("unregistered", None)
        self._mode_key = "dash.mode.production"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Top row: menu button whose items mirror the system-tray menu
        # (populated by MainWindow via set_service_menu with shared QActions).
        top_row = QHBoxLayout()
        self._menu_btn = QToolButton()
        self._menu_btn.setText(tr("dash.menu"))
        self._menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu_btn.setArrowType(Qt.ArrowType.DownArrow)
        top_row.addWidget(self._menu_btn)
        top_row.addStretch()
        layout.addLayout(top_row)

        # Stat grid (4 cards in a horizontal row)
        stat_row = QHBoxLayout()
        stat_row.setSpacing(8)
        self._sip_stat = self._build_stat_group("dash.sip_service", "dash.unstarted")
        self._reg_stat = self._build_stat_group("dash.gateway_reg", "dash.unregistered")
        self._mode_stat = self._build_stat_group("dash.current_mode", "dash.mode.production")
        self._call_stat = self._build_stat_group("dash.call_state", "dash.idle")
        stat_row.addWidget(self._sip_stat)
        stat_row.addWidget(self._reg_stat)
        stat_row.addWidget(self._mode_stat)
        stat_row.addWidget(self._call_stat)
        layout.addLayout(stat_row)

        # Audio device routing group
        audio_group = QGroupBox(tr("dash.audio_devices"))
        self._audio_group = audio_group
        ag = QVBoxLayout(audio_group)
        ag.setSpacing(8)

        dev_row = QHBoxLayout()
        dev_row.setSpacing(12)
        self._playback_cb = QComboBox()
        self._capture_cb = QComboBox()
        pb_col = QVBoxLayout()
        pb_col.setSpacing(4)
        self._playback_label = QLabel(tr("dash.playback"))
        pb_col.addWidget(self._playback_label)
        pb_col.addWidget(self._playback_cb)
        cap_col = QVBoxLayout()
        cap_col.setSpacing(4)
        self._capture_label = QLabel(tr("dash.capture"))
        cap_col.addWidget(self._capture_label)
        cap_col.addWidget(self._capture_cb)
        dev_row.addLayout(pb_col)
        dev_row.addLayout(cap_col)
        ag.addLayout(dev_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._refresh_btn = QPushButton(tr("dash.refresh"))
        self._debug_btn = QPushButton(tr("dash.mode.debug.btn"))
        self._prod_btn = QPushButton(tr("dash.mode.production.btn"))
        self._prod_btn.setEnabled(False)  # production is the default
        btn_row.addWidget(self._refresh_btn)
        self._preset_label = QLabel(tr("dash.mode.preset"))
        btn_row.addWidget(self._preset_label)
        btn_row.addWidget(self._debug_btn)
        btn_row.addWidget(self._prod_btn)
        btn_row.addStretch()
        ag.addLayout(btn_row)
        layout.addWidget(audio_group)

        self._refresh_btn.clicked.connect(self._on_refresh)
        self._debug_btn.clicked.connect(lambda: self._on_preset("debug"))
        self._prod_btn.clicked.connect(lambda: self._on_preset("production"))
        self._playback_cb.currentIndexChanged.connect(self._on_device_change)
        self._capture_cb.currentIndexChanged.connect(self._on_device_change)

        # Phone-report group (feature teleflow-phone-report)
        report_group = QGroupBox(tr("dash.phone_report"))
        self._report_group = report_group
        rg = QVBoxLayout(report_group)
        self._report_stat = self._build_stat_group("dash.report_state", "dash.idle")
        rg.addWidget(self._report_stat)
        self._test_report_btn = QPushButton(tr("dash.test_report"))
        self._test_report_btn.clicked.connect(self._fire_test_report)
        rg.addWidget(self._test_report_btn)
        layout.addWidget(report_group)
        self._test_report_cb: Callable[..., None] | None = None

        # Log group
        log_group = QGroupBox(tr("dash.log_group"))
        self._log_group = log_group
        lg = QVBoxLayout(log_group)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)
        self._log_view.setMinimumHeight(180)
        lg.addWidget(self._log_view)
        layout.addWidget(log_group)

        self._populate_devices()

    # -- stat group helpers --

    @staticmethod
    def _build_stat_group(title_key: str, value_key: str) -> QGroupBox:
        g = QGroupBox(tr(title_key))
        vl = QVBoxLayout(g)
        vl.setContentsMargins(8, 8, 8, 8)
        lbl = QLabel(tr(value_key))
        lbl.setObjectName("stat_value")
        font = lbl.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        lbl.setFont(font)
        vl.addWidget(lbl)
        return g

    def _set_stat_value(self, group: QGroupBox, value: str) -> None:
        lbl = group.findChild(QLabel, "stat_value")
        if lbl is not None:
            lbl.setText(value)

    # -- audio device handling --

    def _populate_devices(self) -> None:
        playback_id, capture_id = self._manager.current_selection()
        self._playback_cb.blockSignals(True)
        self._capture_cb.blockSignals(True)
        self._playback_cb.clear()
        for device in self._manager.playback_devices():
            self._playback_cb.addItem(device.name, device.id)
        self._capture_cb.clear()
        # A leading "no capture" entry lets the user pick one-way (downstream
        # only) operation — matching MicroSIP, where the microphone is only
        # opened when an input device is explicitly selected. Its userData is
        # the empty string, which set_selection normalises to "one-way".
        self._capture_cb.addItem(tr("dash.no_capture"), "")
        for device in self._manager.capture_devices():
            self._capture_cb.addItem(device.name, device.id)
        self._select(self._playback_cb, playback_id)
        self._select(self._capture_cb, capture_id)
        self._playback_cb.blockSignals(False)
        self._capture_cb.blockSignals(False)

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
        is_debug = preset == "debug"
        self._debug_btn.setEnabled(not is_debug)
        self._prod_btn.setEnabled(is_debug)
        self._mode_key = "dash.mode.debug" if is_debug else "dash.mode.production"
        self._set_stat_value(self._mode_stat, tr(self._mode_key))

    def _on_device_change(self) -> None:
        playback_id = self._playback_cb.currentData()
        capture_id = self._capture_cb.currentData()
        if playback_id is None or capture_id is None:
            return
        try:
            self._manager.set_selection(playback_id, capture_id)
        except ValueError:
            self._populate_devices()

    # -- log --

    def append_log_line(self, line: str) -> None:
        """Append a log line, auto-scrolling to bottom.

        Lines are colour-coded by category:
          [SIP]/[CALL] → blue, [MEDIA] → green, [AUDIO] → dark yellow, [ERROR] → red
        """
        colour_map = {
            "[SIP]": QColor("#0000cc"),
            "[CALL]": QColor("#0000cc"),
            "[MEDIA]": QColor("#006600"),
            "[AUDIO]": QColor("#996600"),
            "[TTS]": QColor("#663399"),
            "[FFMPEG]": QColor("#663399"),
            "[REPORT]": QColor("#663399"),
            "[RPC]": QColor("#007070"),
            "[ERROR]": QColor("#cc0000"),
        }
        fmt = QTextCharFormat()
        # Unified log lines carry a level token ("… ERROR [HOOK] …"); keep both
        # that rendered form and raw "[ERROR]"-prefixed lines red.
        if " ERROR " in line or "[ERROR]" in line:
            fmt.setForeground(QColor("#cc0000"))
        else:
            for prefix, colour in colour_map.items():
                if prefix in line:
                    fmt.setForeground(colour)
                    break
        self._log_view.setCurrentCharFormat(fmt)
        self._log_view.appendPlainText(line)
        scrollbar = self._log_view.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    # -- public setters called by MainWindow --

    def set_sip_running(self, running: bool) -> None:
        self._sip_running = running
        self._update_sip_stat()

    def set_sip_registration(self, status_key: str, code: int | None = None) -> None:
        """Update the 网关注册 status card (e.g. registered / unregistered / failed)."""
        self._reg_status = (status_key, code)
        text = (
            tr("reg.failed_with_code", code=code)
            if status_key == "failed" and code
            else tr(f"reg.{status_key}")
        )
        self._set_stat_value(self._reg_stat, text)

    def set_service_menu(self, actions: list[QAction]) -> None:
        """Attach the service menu to the top button. ``actions`` are the same
        QAction instances shown in the system-tray menu, so both menus stay in
        sync (labels, enabled state, triggers)."""
        menu = QMenu(self._menu_btn)
        for action in actions:
            menu.addAction(action)
        self._menu_btn.setMenu(menu)

    def set_call_state(self, state: CallState) -> None:
        self._call_state = state
        text = {
            CallState.CONNECTED: tr("dash.call.connected"),
            CallState.INCOMING: tr("dash.call.incoming"),
            CallState.ENDED: tr("dash.call.ended"),
            CallState.IDLE: tr("dash.idle_listening")
            if self._sip_running
            else tr("dash.idle"),
        }.get(state, tr("dash.idle"))
        self._set_stat_value(self._call_stat, text)

    def set_report_state(self, state: ReportState) -> None:
        self._report_state = state
        text = {
            ReportState.IDLE: tr("dash.idle"),
            ReportState.DIALING: tr("dash.report.dialing"),
            ReportState.PLAYING: tr("dash.report.playing"),
            ReportState.COMPLETED: tr("dash.report.completed"),
            ReportState.FAILED: tr("dash.report.failed"),
        }.get(state, tr("dash.idle"))
        self._set_stat_value(self._report_stat, text)

    def set_test_report_callback(self, cb: Callable[..., None]) -> None:
        self._test_report_cb = cb

    def _fire_test_report(self) -> None:
        if self._test_report_cb is not None:
            self._test_report_cb()

    def set_mode(self, mode_key: str) -> None:
        self._mode_key = mode_key
        self._set_stat_value(self._mode_stat, tr(mode_key))

    def _update_sip_stat(self) -> None:
        self._set_stat_value(
            self._sip_stat, tr("dash.running") if self._sip_running else tr("dash.unstarted")
        )

    def retranslate(self) -> None:
        """Re-bind every user-visible string after a language switch."""
        self._menu_btn.setText(tr("dash.menu"))
        self._sip_stat.setTitle(tr("dash.sip_service"))
        self._reg_stat.setTitle(tr("dash.gateway_reg"))
        self._mode_stat.setTitle(tr("dash.current_mode"))
        self._call_stat.setTitle(tr("dash.call_state"))
        self._audio_group.setTitle(tr("dash.audio_devices"))
        self._playback_label.setText(tr("dash.playback"))
        self._capture_label.setText(tr("dash.capture"))
        self._refresh_btn.setText(tr("dash.refresh"))
        self._preset_label.setText(tr("dash.mode.preset"))
        self._debug_btn.setText(tr("dash.mode.debug.btn"))
        self._prod_btn.setText(tr("dash.mode.production.btn"))
        self._report_group.setTitle(tr("dash.phone_report"))
        self._report_stat.setTitle(tr("dash.report_state"))
        self._test_report_btn.setText(tr("dash.test_report"))
        self._log_group.setTitle(tr("dash.log_group"))
        self._populate_devices()
        # Re-render dynamic state text in the new language.
        self._update_sip_stat()
        self.set_sip_registration(*self._reg_status)
        self.set_call_state(self._call_state)
        self.set_report_state(self._report_state)
        self.set_mode(self._mode_key)


# ---------------------------------------------------------------------------
# Settings dialog (modal — opened from tray menu)
# ---------------------------------------------------------------------------
class SettingsDialog(QDialog):
    """Settings modal, opened from the system tray menu."""

    def __init__(
        self,
        manager: AudioDeviceManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._store = manager.store
        self.setWindowTitle(tr("settings.title"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMinimumHeight(460)
        # (widget, i18n-key) pairs re-bound on a language switch.
        self._retranslatable: list[tuple[QLabel | QCheckBox | QPushButton, str]] = []
        self._page_titles: list[str] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Left: section menu  |  Right: grouped config panels (macOS System
        # Settings style). A QListWidget drives a QStackedWidget of pages.
        split = QHBoxLayout()
        split.setSpacing(12)
        self._menu = QListWidget()
        self._menu.setFixedWidth(150)
        self._pages = QStackedWidget()
        split.addWidget(self._menu)
        split.addWidget(self._pages, 1)
        layout.addLayout(split)

        # --- Page: SIP 账号 (客户端注册到外部服务器) ---
        acct_page = QWidget()
        al = QVBoxLayout(acct_page)
        al.setContentsMargins(12, 12, 12, 12)
        al.setSpacing(8)
        # 网关连接:域名或 IP / 端口 / 分机号(分机号也作认证账号)。
        al.addWidget(self._lbl("settings.gateway_addr"))
        self.sip_host = QLineEdit()
        self.sip_host.setPlaceholderText(tr("settings.gateway_addr.ph"))
        al.addWidget(self.sip_host)
        host_port_row = QHBoxLayout()
        host_port_row.setSpacing(6)
        host_port_row.addWidget(self._lbl("settings.gateway_port"))
        self.sip_server_port = QSpinBox()
        self.sip_server_port.setRange(1, 65535)
        self.sip_server_port.setValue(5060)
        host_port_row.addWidget(self.sip_server_port)
        host_port_row.addStretch()
        al.addLayout(host_port_row)
        al.addWidget(self._lbl("settings.extension"))
        self.sip_user = QLineEdit()
        al.addWidget(self.sip_user)
        al.addWidget(self._lbl("settings.sip_password"))
        self.sip_password = QLineEdit()
        self.sip_password.setEchoMode(QLineEdit.EchoMode.Password)
        al.addWidget(self.sip_password)
        al.addWidget(self._lbl("settings.report_hint"))
        # SIP 本地监听端口（选填）: 留空则自动检测空闲端口（避免与本机其他
        # SIP 服务如 FreeSWITCH 抢占同一端口）。
        port_row = QHBoxLayout()
        port_row.setSpacing(6)
        port_row.addWidget(self._lbl("settings.sip_port"))
        self.sip_port = QLineEdit()
        self.sip_port.setPlaceholderText(tr("settings.sip_port.ph"))
        port_row.addWidget(self.sip_port)
        port_row.addStretch()
        al.addLayout(port_row)
        self.sip_auto_connect = self._chk("settings.auto_connect")
        al.addWidget(self.sip_auto_connect)
        al.addStretch()

        # --- Page: 钩子命令 ---
        hook_page = QWidget()
        hl = QVBoxLayout(hook_page)
        hl.setContentsMargins(12, 12, 12, 12)
        hl.setSpacing(8)
        hl.addWidget(self._lbl("settings.off_hook"))
        self.off_hook_cmd = QLineEdit()
        self.off_hook_cmd.setPlaceholderText(tr("settings.off_hook.ph"))
        hl.addWidget(self.off_hook_cmd)
        hl.addWidget(self._lbl("settings.on_hook"))
        self.on_hook_cmd = QLineEdit()
        self.on_hook_cmd.setPlaceholderText(tr("settings.on_hook.ph"))
        hl.addWidget(self.on_hook_cmd)
        hl.addStretch()

        # --- Page: 呼入 IVR ---
        ivr_page = QWidget()
        ivl = QVBoxLayout(ivr_page)
        ivl.setContentsMargins(12, 12, 12, 12)
        ivl.setSpacing(8)
        self.ivr_enabled = self._chk("settings.ivr.enabled")
        ivl.addWidget(self.ivr_enabled)
        ivl.addWidget(self._lbl("settings.ivr.welcome"))
        self.ivr_welcome = QLineEdit()
        self.ivr_welcome.setPlaceholderText(tr("settings.ivr.welcome.ph"))
        ivl.addWidget(self.ivr_welcome)
        ivl.addWidget(self._lbl("settings.ivr.desc"))
        digit_grid = QGridLayout()
        digit_grid.setSpacing(4)
        digit_grid.addWidget(self._lbl("settings.ivr.key"), 0, 0)
        digit_grid.addWidget(self._lbl("settings.ivr.prompt"), 0, 1)
        digit_grid.addWidget(self._lbl("settings.ivr.command"), 0, 2)
        digit_grid.addWidget(self._lbl("settings.ivr.bridge"), 0, 3)
        self.ivr_digit_text_edits: dict[str, QLineEdit] = {}
        self.ivr_digit_hook_edits: dict[str, QLineEdit] = {}
        self.ivr_exit_checkboxes: dict[str, QCheckBox] = {}
        for row, digit in enumerate("1234567890", start=1):
            digit_grid.addWidget(QLabel(digit), row, 0)
            text_edit = QLineEdit()
            text_edit.setPlaceholderText(tr("settings.ivr.text.ph"))
            hook_edit = QLineEdit()
            hook_edit.setPlaceholderText(tr("settings.ivr.cmd.ph"))
            exit_cb = QCheckBox()
            exit_cb.setToolTip(tr("settings.ivr.exit.tip"))
            # Single-select: checking one bridge key unchecks the others.
            exit_cb.toggled.connect(
                lambda checked, d=digit: self._on_exit_digit_toggled(d, checked)
            )
            digit_grid.addWidget(text_edit, row, 1)
            digit_grid.addWidget(hook_edit, row, 2)
            digit_grid.addWidget(exit_cb, row, 3)
            self.ivr_digit_text_edits[digit] = text_edit
            self.ivr_digit_hook_edits[digit] = hook_edit
            self.ivr_exit_checkboxes[digit] = exit_cb
        ivl.addLayout(digit_grid)
        ivl.addStretch()

        # --- Page: 电话汇报 (RPC) ---
        report_scroll = QScrollArea()
        report_scroll.setWidgetResizable(True)
        report_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        report_page = QWidget()
        rp = QVBoxLayout(report_page)
        rp.setContentsMargins(12, 12, 12, 12)
        rp.setSpacing(8)
        report_scroll.setWidget(report_page)
        self.rpc_enabled = self._chk("settings.rpc.enabled")
        self.rpc_port = QSpinBox()
        self.rpc_port.setRange(1, 65535)
        self.rpc_token = QLineEdit()
        self.rpc_token.setReadOnly(True)
        self.rpc_token_reset_btn = QPushButton(tr("settings.reset_token"))
        self.rpc_token_reset_btn.setMinimumWidth(96)
        self._retranslatable.append((self.rpc_token_reset_btn, "settings.reset_token"))
        self.rpc_token_reset_btn.clicked.connect(self._reset_token)
        rpc_token_row = QHBoxLayout()
        rpc_token_row.setSpacing(6)
        rpc_token_row.addWidget(self.rpc_token)
        rpc_token_row.addWidget(self.rpc_token_reset_btn)
        self.report_host = QLineEdit()
        self.report_host.setPlaceholderText(tr("settings.desk_addr.ph"))
        self.report_port = QSpinBox()
        self.report_port.setRange(1, 65535)
        self.report_port.setValue(5060)
        self.report_extension = QLineEdit()
        self.report_extension.setPlaceholderText(tr("settings.ext.ph"))
        self.report_caller_id = QLineEdit()
        self.report_caller_id.setPlaceholderText(tr("settings.caller_id.ph"))
        # TTS voice: a dropdown of built-in edge-tts roles + a "自定义…" entry
        # that reveals a free-text field for any other voice ID.
        self._tts_voice_ids = [vid for _name, vid in BUILTIN_TTS_VOICES]
        self.tts_voice = QComboBox()
        self.tts_voice.currentIndexChanged.connect(self._on_tts_voice_changed)
        self.tts_voice_custom = QLineEdit()
        self.tts_voice_custom.setPlaceholderText(tr("settings.tts_voice_custom.ph"))
        self.tts_voice_custom.hide()
        self.ffmpeg_path = QLineEdit()
        rp.addWidget(self.rpc_enabled)
        rp.addWidget(self._lbl("settings.rpc_port"))
        rp.addWidget(self.rpc_port)
        rp.addWidget(self._lbl("settings.rpc_token"))
        rp.addLayout(rpc_token_row)
        # 分机号: the only required field, shown first with display priority.
        ext_label = QLabel(tr("settings.extension.required"))
        self._retranslatable.append((ext_label, "settings.extension.required"))
        ext_label.setStyleSheet("font-weight: bold; color: #b00020;")
        rp.addWidget(ext_label)
        rp.addWidget(self.report_extension)
        # 座机（选填）: secondary to 分机号; 留空则默认走网关.
        rp.addWidget(self._lbl("settings.desk_addr"))
        rp.addWidget(self.report_host)
        report_port_row = QHBoxLayout()
        report_port_row.setSpacing(6)
        report_port_row.addWidget(self._lbl("settings.desk_port"))
        report_port_row.addWidget(self.report_port)
        report_port_row.addStretch()
        rp.addLayout(report_port_row)
        rp.addWidget(self._lbl("settings.caller_id"))
        rp.addWidget(self.report_caller_id)
        rp.addWidget(self._lbl("settings.tts_voice"))
        rp.addWidget(self.tts_voice)
        rp.addWidget(self.tts_voice_custom)
        rp.addWidget(self._lbl("settings.ffmpeg"))
        rp.addWidget(self.ffmpeg_path)
        # 汇报播报结束后是否自动挂机。
        self.report_hangup_on_eof = self._chk("settings.report_hangup")
        rp.addWidget(self.report_hangup_on_eof)
        # TTS 合成参数: 缓存 TTL 与失败重试次数，均可通过设置调整。
        rp.addWidget(self._lbl("settings.tts_ttl"))
        self.tts_cache_ttl_seconds = QSpinBox()
        self.tts_cache_ttl_seconds.setRange(0, 31_536_000)
        self.tts_cache_ttl_seconds.setValue(604800)
        rp.addWidget(self.tts_cache_ttl_seconds)
        rp.addWidget(self._lbl("settings.tts_retry"))
        self.tts_retry_attempts = QSpinBox()
        self.tts_retry_attempts.setRange(1, 10)
        self.tts_retry_attempts.setValue(3)
        rp.addWidget(self.tts_retry_attempts)
        rp.addStretch()

        # --- Page: 日志与启动 ---
        log_page = QWidget()
        ll = QVBoxLayout(log_page)
        ll.setContentsMargins(12, 12, 12, 12)
        ll.setSpacing(8)
        fl = QFormLayout()
        self.log_level = QComboBox()
        self.log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        fl.addRow(self._lbl("settings.log_level"), self.log_level)
        self.autostart = self._chk("settings.autostart")
        fl.addRow(self.autostart)
        self.start_minimized = self._chk("settings.start_minimized")
        fl.addRow(self.start_minimized)
        # Language: follows the OS when "auto"; persisted in Settings.language.
        self._language_combo = QComboBox()
        self._language_combo.addItem(tr("language.auto"), "auto")
        self._language_combo.addItem(tr("language.english"), "en")
        self._language_combo.addItem(tr("language.chinese"), "zh_CN")
        fl.addRow(self._lbl("settings.language"), self._language_combo)
        ll.addLayout(fl)
        ll.addStretch()

        for title_key, page in [
            ("settings.sip_account", acct_page),
            ("settings.hooks", hook_page),
            ("settings.ivr", ivr_page),
            ("settings.report", report_scroll),
            ("settings.log_start", log_page),
        ]:
            self._menu.addItem(tr(title_key))
            self._pages.addWidget(page)
            self._page_titles.append(title_key)
        self._menu.currentRowChanged.connect(self._pages.setCurrentIndex)
        self._menu.setCurrentRow(0)

        self._populate_tts_voice_combo()
        self._refresh_ffmpeg_placeholder()

        # Buttons (full width, below the split panes)
        btn_row = QHBoxLayout()
        self.save_btn = QPushButton(tr("settings.save"))
        self._retranslatable.append((self.save_btn, "settings.save"))
        self.save_btn.clicked.connect(self._save_and_close)
        self.cancel_btn = QPushButton(tr("settings.close"))
        self._retranslatable.append((self.cancel_btn, "settings.close"))
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self._load_settings()

    def _load_settings(self) -> None:
        settings = self._store.load()
        self.sip_host.setText(settings.sip_host)
        self.sip_server_port.setValue(settings.sip_server_port)
        self.sip_user.setText(settings.sip_user)
        self.sip_password.setText(settings.sip_password)
        self.sip_port.setText(settings.sip_port)
        self.sip_auto_connect.setChecked(settings.sip_auto_connect)
        self.off_hook_cmd.setText(settings.off_hook_cmd)
        self.on_hook_cmd.setText(settings.on_hook_cmd)
        self.ivr_enabled.setChecked(settings.ivr_enabled)
        self.ivr_welcome.setText(settings.ivr_welcome)
        for digit, edit in self.ivr_digit_text_edits.items():
            edit.setText(settings.ivr_digit_text.get(digit, ""))
        for digit, edit in self.ivr_digit_hook_edits.items():
            edit.setText(settings.ivr_digit_hook.get(digit, ""))
        for digit, cb in self.ivr_exit_checkboxes.items():
            cb.setChecked(digit == settings.ivr_exit_digit)
        self.rpc_enabled.setChecked(settings.rpc_enabled)
        self.rpc_port.setValue(settings.rpc_port)
        self.rpc_token.setText(settings.rpc_token)
        self.report_host.setText(settings.report_host)
        self.report_port.setValue(settings.report_port)
        self.report_extension.setText(settings.report_extension)
        self.report_caller_id.setText(settings.report_caller_id)
        self._select_tts_voice(settings.tts_voice)
        self.report_hangup_on_eof.setChecked(settings.report_hangup_on_eof)
        self.tts_cache_ttl_seconds.setValue(settings.tts_cache_ttl_seconds)
        self.tts_retry_attempts.setValue(settings.tts_retry_attempts)
        self.ffmpeg_path.setText(settings.ffmpeg_path)
        self.log_level.setCurrentText(settings.log_level)
        self.autostart.setChecked(settings.autostart)
        self.start_minimized.setChecked(settings.start_minimized)
        lang = settings.language
        idx = self._language_combo.findData(lang)
        if idx < 0:
            idx = self._language_combo.findData("auto")
        self._language_combo.setCurrentIndex(idx)

    def _save_and_close(self) -> None:
        settings = self._store.load()
        settings.sip_host = self.sip_host.text().strip()
        settings.sip_server_port = self.sip_server_port.value()
        settings.sip_user = self.sip_user.text().strip()
        settings.sip_password = self.sip_password.text()
        settings.sip_port = self.sip_port.text().strip()
        settings.sip_auto_connect = self.sip_auto_connect.isChecked()
        settings.off_hook_cmd = self.off_hook_cmd.text().strip()
        settings.on_hook_cmd = self.on_hook_cmd.text().strip()
        settings.ivr_enabled = self.ivr_enabled.isChecked()
        settings.ivr_welcome = self.ivr_welcome.text().strip()
        settings.ivr_digit_text = {
            d: v for d, e in self.ivr_digit_text_edits.items() if (v := e.text().strip())
        }
        settings.ivr_digit_hook = {
            d: v for d, e in self.ivr_digit_hook_edits.items() if (v := e.text().strip())
        }
        # Single bridge/exit digit (at most one row checked); "" = no key bridges.
        exit_digit = ""
        for digit, cb in self.ivr_exit_checkboxes.items():
            if cb.isChecked():
                exit_digit = digit
                break
        settings.ivr_exit_digit = exit_digit
        settings.rpc_enabled = self.rpc_enabled.isChecked()
        settings.rpc_port = self.rpc_port.value()
        settings.rpc_token = self.rpc_token.text().strip()
        settings.report_host = self.report_host.text().strip()
        settings.report_port = self.report_port.value()
        settings.report_extension = self.report_extension.text().strip()
        settings.report_caller_id = self.report_caller_id.text().strip()
        settings.tts_voice = self._current_tts_voice()
        settings.report_hangup_on_eof = self.report_hangup_on_eof.isChecked()
        settings.tts_cache_ttl_seconds = self.tts_cache_ttl_seconds.value()
        settings.tts_retry_attempts = self.tts_retry_attempts.value()
        settings.ffmpeg_path = self.ffmpeg_path.text().strip()
        settings.log_level = self.log_level.currentText()
        settings.autostart = self.autostart.isChecked()
        settings.start_minimized = self.start_minimized.isChecked()
        settings.language = self._language_combo.currentData() or "auto"
        self._store.save(settings)
        self.retranslate()
        set_language(settings.language)
        self.accept()

    def _on_exit_digit_toggled(self, digit: str, checked: bool) -> None:
        """Single-select the bridge/exit digit: checking one row clears the rest.

        A checked row means "pressing this key exits the IVR menu and bridges the
        call two-way"; at most one key can be the bridge key (stored in
        ``ivr_exit_digit``). Unchecking the only checked row leaves no bridge key.
        """
        if not checked:
            return
        for other, cb in self.ivr_exit_checkboxes.items():
            if other != digit:
                cb.setChecked(False)

    def _on_tts_voice_changed(self, idx: int) -> None:
        """Show the custom voice-ID field only while the '自定义…' entry is selected."""
        self.tts_voice_custom.setVisible(idx == len(self._tts_voice_ids))

    def _current_tts_voice(self) -> str:
        """Return the chosen edge-tts voice ID — a built-in selection, or the
        custom field's text when the custom entry is the active one."""
        if self.tts_voice.currentIndex() == len(self._tts_voice_ids):
            return self.tts_voice_custom.text().strip()
        return self._tts_voice_ids[self.tts_voice.currentIndex()]

    def _select_tts_voice(self, value: str) -> None:
        """Restore the dropdown to ``value`` when it's a built-in ID; otherwise
        switch to the custom entry and fill the free-text field with it."""
        if value in self._tts_voice_ids:
            self.tts_voice.setCurrentIndex(self._tts_voice_ids.index(value))
            self.tts_voice_custom.clear()
        else:
            self.tts_voice.setCurrentIndex(len(self._tts_voice_ids))
            self.tts_voice_custom.setText(value)

    def _reset_token(self) -> None:
        """Generate a fresh random RPC token (writes on Save)."""
        import secrets

        self.rpc_token.setText(secrets.token_hex(16))

    def _lbl(self, key: str) -> QLabel:
        lbl = QLabel(tr(key))
        self._retranslatable.append((lbl, key))
        return lbl

    def _chk(self, key: str) -> QCheckBox:
        chk = QCheckBox(tr(key))
        self._retranslatable.append((chk, key))
        return chk

    def _populate_tts_voice_combo(self, preserve: bool = False) -> None:
        """Build the TTS voice dropdown from built-in edge-tts roles (+ 自定义…).

        Voice *display names* are translated via the i18n catalog; the stored
        ``tts_voice`` field is always the raw edge-tts voice ID. When ``preserve``
        is set the current selection is restored after rebuilding (used on a
        language switch so the user's choice doesn't reset to the first entry).
        """
        current = self._current_tts_voice() if preserve else None
        self.tts_voice.blockSignals(True)
        self.tts_voice.clear()
        for name, vid in BUILTIN_TTS_VOICES:
            key = _TTS_VOICE_KEYS.get(name, "tts.voice.xiaoxiao")
            self.tts_voice.addItem(f"{tr(key)} — {vid}", vid)
        self.tts_voice.addItem(tr("settings.custom"))
        if current is not None:
            self._select_tts_voice(current)
        self.tts_voice.blockSignals(False)
        self._on_tts_voice_changed(self.tts_voice.currentIndex())

    def _refresh_ffmpeg_placeholder(self) -> None:
        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            self.ffmpeg_path.setPlaceholderText(tr("settings.ffmpeg.auto", path=ffmpeg_bin))
        else:
            self.ffmpeg_path.setPlaceholderText(tr("settings.ffmpeg.find"))

    def retranslate(self) -> None:
        """Re-bind every user-visible string after a language switch."""
        self.setWindowTitle(tr("settings.title"))
        for widget, key in self._retranslatable:
            widget.setText(tr(key))
        for i, title_key in enumerate(self._page_titles):
            item = self._menu.item(i)
            if item is not None:
                item.setText(tr(title_key))
        self.sip_host.setPlaceholderText(tr("settings.gateway_addr.ph"))
        self.off_hook_cmd.setPlaceholderText(tr("settings.off_hook.ph"))
        self.on_hook_cmd.setPlaceholderText(tr("settings.on_hook.ph"))
        self.ivr_welcome.setPlaceholderText(tr("settings.ivr.welcome.ph"))
        self.sip_port.setPlaceholderText(tr("settings.sip_port.ph"))
        self.report_extension.setPlaceholderText(tr("settings.ext.ph"))
        self.report_host.setPlaceholderText(tr("settings.desk_addr.ph"))
        self.report_caller_id.setPlaceholderText(tr("settings.caller_id.ph"))
        self.tts_voice_custom.setPlaceholderText(tr("settings.tts_voice_custom.ph"))
        for edit in self.ivr_digit_text_edits.values():
            edit.setPlaceholderText(tr("settings.ivr.text.ph"))
        for edit in self.ivr_digit_hook_edits.values():
            edit.setPlaceholderText(tr("settings.ivr.cmd.ph"))
        for cb in self.ivr_exit_checkboxes.values():
            cb.setToolTip(tr("settings.ivr.exit.tip"))
        self._populate_tts_voice_combo(preserve=True)
        self._refresh_ffmpeg_placeholder()
        self._language_combo.setItemText(0, tr("language.auto"))
        self._language_combo.setItemText(1, tr("language.english"))
        self._language_combo.setItemText(2, tr("language.chinese"))



# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class _RpcCall:
    """One blocking RPC dispatch onto the GUI thread.

    The RPC server hands report/play/replay work to a scheduler so it runs on
    the Qt main thread (pjsua2 is not thread-safe; see ``scheduler`` above).
    ``done`` is set once the main thread ran the call; ``error`` re-raises any
    exception on the waiting HTTP thread so status codes still map correctly.
    """

    __slots__ = ("fn", "value", "error", "done")

    def __init__(self, fn: Callable[[], object]) -> None:
        self.fn = fn
        self.value: object | None = None
        self.error: BaseException | None = None
        self.done = threading.Event()


class MainWindow(QMainWindow):
    # Worker->GUI handoff for the async test-report path: emitted from the
    # synthesis thread, delivered as a queued call on the GUI thread (PyQt6's
    # QTimer.singleShot has no receiver overload, signals are the safe route).
    _synth_done = pyqtSignal()
    _synth_timed_out = pyqtSignal()
    # Any-thread -> GUI-thread dispatcher. pjsua2 callbacks (invite/bye/dtmf/
    # media-state) fire on pjsua2's worker threads and log lines / dashboard
    # updates flow from there; PyQt widgets must only be touched on the GUI
    # thread, or QPlainTextEdit etc. can access-violate (Qt6Gui.dll 0xc0000005).
    _gui_task = pyqtSignal(object)
    # Blocking RPC dispatcher: an HTTP handler thread waits on the call while
    # it executes on the GUI thread (see _RpcCall / rpc_scheduler below).
    _rpc_task = pyqtSignal(object)

    def __init__(
        self,
        manager: AudioDeviceManager,
        service: SipCoreService,
        store: ConfigStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._service = service
        self._store = store
        # Apply the persisted (or auto) language before building any widget so
        # every tr() during construction resolves against the right locale.
        set_language(store.load().language)
        self._force_quit = False
        self._tray: QSystemTrayIcon | None = None
        self._tray_sip: QAction | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._logger: EventLogger | None = None
        # Worker->GUI handoff for the async test-report path (_test_report).
        self._pending_report_text = ""
        self._pending_report_mp3 = ""
        self._pending_report_wav = ""
        self._pending_report_error: str | None = None
        self._synth_done.connect(self._finish_test_report)
        self._synth_timed_out.connect(self._log_synth_timeout)
        self._gui_task.connect(self._run_gui_task)
        self._rpc_task.connect(self._run_rpc_task)
        self.setWindowTitle(tr("app.title"))
        self.setWindowIcon(_load_icon())
        self.resize(680, 520)

        # Dashboard as central widget
        self.dashboard = DashboardWidget(manager)
        self.setCentralWidget(self.dashboard)

        self._build_service_actions()
        self.dashboard.set_service_menu(
            [
                self._act_toggle_sip,
                self._act_show,
                self._act_settings,
                self._act_report,
                self._act_quit,
            ]
        )
        self._setup_tray()
        self._wire_service()
        register_on_change(self._retranslate_all)

    def gui(self, fn: Callable[[], None]) -> None:
        """Queued-handoff ``fn`` onto the GUI thread.

        Safe to call from any thread: pjsua2 worker threads and hook threads
        must never touch widgets directly; the queued signal runs ``fn`` on the
        Qt main thread. From the GUI thread itself the call is direct.
        """
        self._gui_task.emit(fn)

    def _run_gui_task(self, fn: Callable[[], None]) -> None:
        fn()

    def rpc_scheduler(self, fn: Callable[[], object]) -> object:
        """Blocking marshal of an RPC-triggered service call onto the GUI thread.

        ``RpcServer`` hands report/play/replay work here so pjsua2 calls (e.g.
        an outbound ``makeCall``) never run on the HTTP handler thread: that
        used to deadlock against pjsua2's worker thread while it processed the
        peer's 407 auth challenge, freezing the whole app (the pjsua log cut
        off mid-line) and leaving the report slot wedged. The handler waits on
        ``_RpcCall.done`` so the reply is sent only after the call finished and
        exceptions still map to the documented HTTP status codes.
        """
        call = _RpcCall(fn)
        self._rpc_task.emit(call)
        call.done.wait()
        if call.error is not None:
            raise call.error
        return call.value

    def _run_rpc_task(self, call: _RpcCall) -> None:
        try:
            call.value = call.fn()
        except Exception as exc:  # noqa: BLE001 - re-raised on the HTTP thread
            call.error = exc
        finally:
            call.done.set()

    def append_log_line(self, line: str) -> None:
        """Public log entry: routed through the unified logger so the UI panel
        and the log file record exactly the same lines. Without a logger (tests)
        it falls back to UI-only."""
        if self._logger is not None:
            self._logger.log_line(line)
        else:
            self._append_ui_log_line(line)

    def _append_ui_log_line(self, line: str) -> None:
        """UI-panel-only append; this is the EventLogger sink target."""
        self.gui(lambda: self.dashboard.append_log_line(line))

    def attach_logger(self, logger: EventLogger) -> None:
        """Wire the unified logger; append_log_line then reaches file + UI."""
        self._logger = logger

    def _build_service_actions(self) -> None:
        """One set of QActions shared by the dashboard menu and the system-tray
        menu, so both always show the same items in the same state."""
        self._act_toggle_sip = QAction(self)
        self._act_toggle_sip.triggered.connect(self._toggle_sip)
        self._act_show = QAction(self)
        self._act_show.triggered.connect(self.show_window)
        self._act_settings = QAction(self)
        self._act_settings.triggered.connect(self._open_settings)
        self._act_report = QAction(self)
        self._act_report.triggered.connect(self._test_report)
        self._act_quit = QAction(self)
        self._act_quit.triggered.connect(self.quit_app)
        self._update_action_texts()

    def _update_action_texts(self) -> None:
        """Re-bind the shared menu/tray action labels (toggle label tracks state)."""
        running = self._service.running
        self._act_toggle_sip.setText(
            tr("action.toggle_sip.stop" if running else "action.toggle_sip.start")
        )
        self._act_show.setText(tr("action.show"))
        self._act_settings.setText(tr("action.settings"))
        self._act_report.setText(tr("action.test_report"))
        self._act_quit.setText(tr("action.quit"))

    def _retranslate_all(self, resolved: str) -> None:
        """Language-change callback (registered with ``register_on_change``).

        ``resolved`` is the new locale; we ignore it and just re-bind every
        user-visible string (window, shared actions, dashboard, open dialog).
        """
        self.setWindowTitle(tr("app.title"))
        self._update_action_texts()
        self.dashboard.retranslate()
        if self._settings_dialog is not None:
            self._settings_dialog.retranslate()

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_load_tray_icon())
        menu = QMenu()
        menu.addAction(self._act_toggle_sip)
        menu.addAction(self._act_show)
        menu.addAction(self._act_settings)
        menu.addAction(self._act_report)
        menu.addSeparator()
        menu.addAction(self._act_quit)
        self._tray_sip = self._act_toggle_sip
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda reason: self.show_window()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        self._tray.show()

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._force_quit = True
        # Tear down before leaving the event loop: quitting with a live pjsua2
        # stack and/or the tray menu still open has crashed Qt6Gui.dll on this
        # Windows build (access violation, exit code 0xC0000005). _cleanup is
        # also wired to aboutToQuit, so the service stops before any widget is
        # destroyed even if this path is bypassed.
        self._cleanup()
        QApplication.processEvents()
        QApplication.quit()

    def _open_settings(self) -> None:
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self._manager, self)
        self._settings_dialog.exec()

    def _test_report(self) -> None:
        """Trigger a phone report with built-in sample text (from the tray menu
        or the dashboard button).

        TTS synthesis + ffmpeg transcoding run in a background thread so the
        UI stays responsive. When the wav is ready we hop back to the GUI
        thread via QTimer.singleShot(msec, self, "slot") — the string-slot
        overload posts a queued call to ``self``'s thread, which is the only
        safe way to touch Qt widgets / pjsua2 from the worker.
        """
        text = tr("report.test_sample")
        settings = self._store.load()
        voice, ffmpeg = settings.tts_voice, settings.ffmpeg_path
        self.append_log_line(f"[TTS] synthesizing: voice={voice}")
        self._pending_report_text = text

        def _synthesize() -> None:
            try:
                from teleflow.tts import CachingTtsBackend, EdgeTtsBackend

                tts = CachingTtsBackend(
                    EdgeTtsBackend(
                        ffmpeg_path=ffmpeg,
                        retry_attempts=settings.tts_retry_attempts,
                        logger=self.append_log_line,
                    ),
                    logger=self.append_log_line,
                    cache_ttl_seconds=settings.tts_cache_ttl_seconds,
                )
                # Unified conversion path: cache + TTL applied (keyed by text+voice).
                wav = tts.synthesize_to_wav(text, voice, prefix="report")
                self._pending_report_mp3 = str(wav)
                self._pending_report_wav = str(wav)
                self._pending_report_error = None
            except Exception as exc:  # noqa: BLE001 - surface failures in the log
                self._pending_report_error = str(exc)
            self._synth_done.emit()

        threading.Thread(target=_synthesize, daemon=True).start()
        threading.Thread(
            target=self._watchdog_test_report,
            args=(120,),
            daemon=True,
        ).start()

    def _watchdog_test_report(self, timeout_sec: float) -> None:
        """Log a warning if synthesis hangs (e.g. edge-tts network stall)."""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self._pending_report_wav or self._pending_report_error:
                return
            time.sleep(1)
        self._synth_timed_out.emit()

    def _log_synth_timeout(self) -> None:
        self.append_log_line(
            "[TTS] synthesis timed out (>120s); check network and retry"
        )

    def _finish_test_report(self) -> None:
        """GUI-thread continuation of the background synthesis."""
        if self._pending_report_error:
            self.append_log_line(f"[REPORT] test report failed: {self._pending_report_error}")
            self._pending_report_error = None
            return
        mp3, wav = self._pending_report_mp3, self._pending_report_wav
        self.append_log_line(f"[TTS] synthesized: {mp3}")
        self.append_log_line(f"[FFMPEG] transcoded: {wav}")
        self._pending_report_mp3 = ""
        self._pending_report_wav = ""
        try:
            self._service.start_report(self._pending_report_text, audio_path=wav)
        except Exception as exc:  # noqa: BLE001 - surface in the log view
            self.append_log_line(f"[REPORT] test report failed: {exc}")

    def _notify_port_conflict(self, requested: int, selected: int) -> None:
        """Warn the user that the preferred SIP port was occupied and the client
        auto-moved to a free one (the log line is written by the service)."""
        if self._tray is not None:
            self._tray.showMessage(
                tr("tray.register.title"),
                tr("tray.port_conflict", requested=requested, selected=selected),
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )

    def _notify_rpc_port_conflict(self, requested: int, selected: int) -> None:
        """The configured local control port was occupied; RPC auto-moved to a
        free one. Surface that to the user so external callers can be repointed
        (the [RPC][WARN] log line is written by RpcServer.start)."""
        if self._tray is not None:
            self._tray.showMessage(
                tr("tray.rpc.title"),
                tr("tray.rpc_port_conflict", requested=requested, selected=selected),
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )

    def _notify_rpc_port_unavailable(self, requested: int) -> None:
        """No free port was found in the scan window, so the local control
        interface did not start. Tell the user plainly so they can free the
        port or pick another one in Settings."""
        if self._tray is not None:
            self._tray.showMessage(
                tr("tray.rpc.title"),
                tr("tray.rpc_port_unavailable", requested=requested),
                QSystemTrayIcon.MessageIcon.Warning,
                8000,
            )

    def _wire_service(self) -> None:
        svc = self._service
        # All handlers are marshaled to the GUI thread via self.gui: service
        # events fire from pjsua2 worker threads on a live call, and touching
        # dashboard widgets there crashes Qt (Qt6Gui.dll access violation).
        svc.on(EVENT_SIP_STARTED, lambda: self.gui(self._sync_sip_button))
        svc.on(EVENT_SIP_STOPPED, lambda: self.gui(self._sync_sip_button))
        svc.on(
            EVENT_SIP_REGISTERED,
            lambda contact: self.gui(lambda: self.dashboard.set_sip_registration("registered")),
        )
        svc.on(
            EVENT_SIP_UNREGISTERED,
            lambda: self.gui(lambda: self.dashboard.set_sip_registration("unregistered")),
        )
        svc.on(
            EVENT_SIP_REGISTER_FAILED,
            lambda code, reason: self.gui(
                lambda: self.dashboard.set_sip_registration("failed", code if code else None)
            ),
        )
        svc.on(
            EVENT_SIP_PORT_CONFLICT,
            lambda requested, selected: self.gui(
                lambda: self._notify_port_conflict(requested, selected)
            ),
        )
        svc.on(
            EVENT_CALL_INCOMING,
            lambda call_id: self.gui(lambda: self.dashboard.set_call_state(CallState.INCOMING)),
        )
        svc.on(
            EVENT_CALL_CONNECTED,
            lambda call_id: self.gui(lambda: self.dashboard.set_call_state(CallState.CONNECTED)),
        )
        svc.on(
            EVENT_CALL_ENDED,
            lambda call_id, last_digit="": self.gui(lambda: self.dashboard.set_call_state(CallState.ENDED)),
        )
        svc.on(
            EVENT_REPORT_STARTED,
            lambda report_id, target: self.gui(lambda: self.dashboard.set_report_state(ReportState.DIALING)),
        )
        svc.on(
            EVENT_REPORT_CONNECTED,
            lambda call_id: self.gui(lambda: self.dashboard.set_report_state(ReportState.PLAYING)),
        )
        svc.on(
            EVENT_REPORT_PLAYING,
            lambda call_id: self.gui(lambda: self.dashboard.set_report_state(ReportState.PLAYING)),
        )
        svc.on(
            EVENT_REPORT_COMPLETED,
            lambda report_id, call_id: self.gui(lambda: self.dashboard.set_report_state(ReportState.COMPLETED)),
        )
        svc.on(
            EVENT_REPORT_FAILED,
            lambda reason, report_id: self.gui(lambda: self.dashboard.set_report_state(ReportState.FAILED)),
        )
        self.dashboard.set_test_report_callback(self._test_report)
        self._sync_sip_button()

    def _toggle_sip(self) -> None:
        if self._service.running:
            self._service.stop()
        else:
            try:
                self._service.start()
            except Exception as exc:  # noqa: BLE001 - surface startup failures
                self.append_log_line(f"[SIP] start failed: {exc}")
                self._sync_sip_button()
                return
        # Remember the service state so the next launch restores it: started =>
        # auto-connect on launch, stopped => stay stopped ("记录上次状态").
        self._save_auto_connect()
        self._sync_sip_button()

    def _save_auto_connect(self) -> None:
        settings = self._store.load()
        settings.sip_auto_connect = self._service.running
        self._store.save(settings)

    def _sync_sip_button(self) -> None:
        running = self._service.running
        self.dashboard.set_sip_running(running)
        if not running:
            self.dashboard.set_sip_registration("unregistered")
        self._update_action_texts()

    def _cleanup(self) -> None:
        """Tear down native resources before Qt destroys the widgets.

        Called from ``aboutToQuit`` (the safe, pre-destruction point) and from
        the explicit quit path. Stopping the SIP stack here — rather than during
        widget teardown — is what prevents the shutdown crash: previously the
        pjsua2 threads/objects were still live when the main window was
        destroyed, and an exception raised in that half-torn-down state
        segfaulted PyQt's default error printer while it cleared the exception.
        """
        try:
            if self._service.running:
                self._service.stop()
        except Exception as exc:  # noqa: BLE001 - teardown must not raise into Qt/sip
            self.append_log_line(f"[SIP] stop failed during shutdown: {exc}")
        tray = self._tray
        if tray is not None:
            try:
                menu = tray.contextMenu()
                if menu is not None and menu.isVisible():
                    menu.close()
            except Exception:  # noqa: BLE001 - never let teardown raise into sip
                pass

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # The window-close button / Cmd+W minimizes to the tray (ignore + hide).
        # An application quit (Cmd+Q, or the Quit menu action) must terminate; we
        # detect it via _force_quit / closingDown and tear down instead.
        try:
            if (
                self._tray is not None
                and not self._force_quit
                and not QApplication.closingDown()
            ):
                event.ignore()
                self.hide()
                return
            self._cleanup()
            event.accept()
        except Exception:  # noqa: BLE001 - a teardown exception must never escape into sip
            event.accept()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def _default_audio_backend() -> AudioBackend:
    try:
        return PortAudioBackend()
    except RuntimeError:
        warnings.warn(
            "pjsua2 unavailable; falling back to fake audio backend",
            stacklevel=2,
        )
        return FakeAudioBackend()


def _default_sip_backend() -> SipBackend:
    try:
        return Pjsua2Backend(ConfigStore())
    except RuntimeError:
        warnings.warn("pjsua2 unavailable; using fake SIP backend.")
        return FakeSipBackend()


def maybe_auto_start_sip(
    service: SipCoreService,
    store: ConfigStore,
    log: Callable[[str], None],
) -> bool:
    """Auto-connect the gateway on launch, restoring the last service state.

    Starts the SIP service when the persisted ``sip_auto_connect`` flag is set
    and the gateway config is complete (server + user). On an incomplete
    config or a startup exception the service stays stopped and the flag is
    persisted as False so the next launch doesn't retry the same failure.

    Returns whether the service was (asked to be) started.
    """
    settings = store.load()
    if not settings.sip_auto_connect:
        return False
    if not (settings.sip_host and settings.sip_user):
        settings.sip_auto_connect = False
        store.save(settings)
        log("[SIP] gateway config incomplete; not auto-connecting. Complete SIP account settings first.")
        return False
    try:
        service.start()
    except Exception as exc:  # noqa: BLE001 - startup failures must not crash the app
        settings.sip_auto_connect = False
        store.save(settings)
        log(f"[SIP] auto-connect gateway failed: {exc}")
        return False
    return True


class _SignalWaiter(threading.Thread):
    """Consume Ctrl+C / ``kill`` via sigwait, then quit the Qt loop cleanly.

    ``build_app`` blocks SIGINT/SIGTERM in every thread *before* spawning any
    background thread, so this waiter is the one thread that can receive them.
    Without it, a terminal's Ctrl+C can be picked up by a pjsua2 native worker
    thread where Python's main-thread-only signal handling either silently drops
    the signal ("no response") or trips at the wrong moment (the shutdown
    segfault). The quit is queued onto the main thread's event loop, so teardown
    follows the exact same path as a normal app exit, including stopping the SIP
    stack via ``aboutToQuit`` before any widget is destroyed.
    """

    def __init__(self, app: QApplication) -> None:
        super().__init__(name="teleflow-sigwait", daemon=True)
        self._app = app

    def run(self) -> None:
        try:
            signal.pthread_sigmask(
                signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM}
            )
        except (AttributeError, OSError, ValueError):
            return  # no mask support (e.g. Windows): nothing to consume here
        try:
            signal.sigwait({signal.SIGINT, signal.SIGTERM})
        except (ValueError, OSError, RuntimeError):
            return
        QMetaObject.invokeMethod(
            self._app, "quit", Qt.ConnectionType.QueuedConnection
        )


def build_app(
    config_path: Path | None = None,
    audio_backend: AudioBackend | None = None,
    sip_backend: SipBackend | None = None,
) -> QApplication:
    # Force SIGINT/SIGTERM to stay *blocked* in every thread. A terminal's
    # Ctrl+C sends a SIGINT to the process group; the kernel may pick any thread,
    # and when that is a pjsua2 native worker (media/ev_thread) the signal is
    # either silently dropped or trips Python's main-thread-only handler
    # machinery at the wrong moment — both used to make Ctrl+C do nothing or
    # crash the app. Blocking here means every thread created later (pjsua2's
    # workers, Qt's, the RPC server, TTS/hook threads) inherits the blocked
    # mask, leaving the _SignalWaiter thread as the only consumer via sigwait.
    try:
        signal.pthread_sigmask(
            signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM}
        )
    except (AttributeError, OSError, ValueError):
        pass

    app = QApplication([])
    app.setWindowIcon(_load_icon())
    store = ConfigStore(config_path)
    settings = store.load()
    audio_backend = audio_backend or _default_audio_backend()
    sip_backend = sip_backend or _default_sip_backend()
    manager = AudioDeviceManager(audio_backend, store)
    tts = CachingTtsBackend(
        EdgeTtsBackend(
            ffmpeg_path=settings.ffmpeg_path,
            retry_attempts=settings.tts_retry_attempts,
        ),
        cache_ttl_seconds=settings.tts_cache_ttl_seconds,
    )
    service = SipCoreService(sip_backend, store, tts=tts)
    # tts.logger is wired to the unified logger below, after it exists.
    window = MainWindow(manager, service, store)

    # Route the macOS Apple "Quit" (Cmd+Q) into the same teardown as the tray
    # Quit action. By default closeEvent hides to the tray, which leaves the SIP
    # stack and its native threads running until a force-kill. Marking the window
    # force-quit on the application-level Close event makes closeEvent accept and
    # run _cleanup via aboutToQuit.
    class _AppQuitFilter(QObject):
        def __init__(self, window: "MainWindow") -> None:
            super().__init__()
            self._window = window

        def eventFilter(self, obj: object, event: QEvent) -> bool:  # type: ignore[override]
            if obj is app and event.type() == QEvent.Type.Close:
                self._window._force_quit = True
            return False

    app.installEventFilter(_AppQuitFilter(window))
    app.aboutToQuit.connect(window._cleanup)

    # A terminal's Ctrl+C (SIGINT) and `kill` (SIGTERM) are consumed by a
    # dedicated waiter thread (see _SignalWaiter) rather than raising
    # KeyboardInterrupt inside the Qt event loop: an uncaught exception there
    # used to segfault while PyQt cleared the exception that pinned freed
    # Qt/pjsua2 objects during interpreter shutdown. The signal-mask block at the
    # top of build_app keeps these signals out of every other thread (including
    # the pjsua2 workers), so sigwait below is the one deterministic consumer.
    _SignalWaiter(app).start()

    # Unified log API: one EventLogger writes EVERY line to the log file and to
    # the UI panel (via the UI-only sink), so both always record the same
    # content. The terminal may additionally show more (pjsua2's own C logs,
    # request traffic) — that extra noise is expected, not a drift.
    logger = EventLogger(
        level=LogLevel[settings.log_level],
        sink=window._append_ui_log_line,
    )
    window.attach_logger(logger)
    attach(logger, service, manager)

    # Route the service's ad-hoc lines ([IVR]/[REPORT]/[TTS]/[FFMPEG]) and the
    # hook runner's [HOOK] lines through the same logger (file + UI panel).
    service._log = logger.log_line
    # pjsua2 API calls must not re-enter from a callback thread: the report
    # EOF's hangup used to wedge the whole app (report_in_progress stuck true
    # after every report). gui() schedules it on the Qt main thread,
    # non-blocking for the callback thread.
    service._defer = window.gui
    tts.logger = logger.log_line

    # Re-route a live call when the user switches the playback/capture device.
    def _on_device_selected(_playback: str, _capture: str) -> None:
        service.reroute()

    manager.on(EVENT_DEVICE_SELECTED, _on_device_selected)

    # Resilience: on audio-device hotplug, re-enumerate and re-route a live call.
    def _on_audio_devices_changed() -> None:
        manager.handle_hotplug()
        service.reroute_if_connected()

    service.set_device_change_callback(_on_audio_devices_changed)

    # Hook commands: run the user-configured 摘机 command when the current SIP
    # auto-answers an incoming call. Non-blocking; failures are logged, never
    # raised into the call path.
    hook_runner = SubprocessHookRunner(store, log=logger.log_line)
    attach_hooks(service, hook_runner, store)

    # Local loopback RPC control channel (feature teleflow-phone-report). Bound to
    # 127.0.0.1 only, bearer-token authenticated. Skipped if disabled in settings.
    # The scheduler runs every RPC-triggered service call (report/play/replay)
    # on the Qt main thread: pjsua2 is not thread-safe, and an outbound report
    # call driven from an HTTP handler thread used to deadlock the whole app
    # against pjsua2's worker thread (see MainWindow.rpc_scheduler).
    rpc = RpcServer(service, store, log=logger.log_line, scheduler=window.rpc_scheduler)
    rpc.start()
    # A busy RPC port no longer crashes the app (RpcServer walks to a free one,
    # or disables RPC). Either way, tell the user via a tray warning so a
    # duplicate instance / leftover listener isn't silent. Deferred to the GUI
    # thread so it shows once the event loop is running.
    conflict = rpc.port_conflict
    if conflict is not None:
        requested, selected = conflict
        if selected is None:
            window.gui(lambda: window._notify_rpc_port_unavailable(requested))
        else:
            window.gui(lambda: window._notify_rpc_port_conflict(requested, selected))

    if settings.autostart:
        set_autostart(True)
    else:
        set_autostart(False)

    if settings.start_minimized:
        window.hide()
    else:
        window.show()

    # Restore the last service state: auto-connect the gateway on launch when
    # the config is complete; a failed or incomplete auto-start stays stopped.
    maybe_auto_start_sip(service, store, window.append_log_line)

    return app


def main() -> None:
    build_app().exec()


if __name__ == "__main__":
    main()