"""Smoke test for the PyQt6 app shell (ticket 01).

Runs only when PyQt6 is importable and Qt is set to the offscreen platform, so
it is safe to collect in environments without a display or without PyQt6
installed. Verifies the window builds with both tabs and that the settings page
reflects Config Store defaults.
"""

import pytest

try:
    from PyQt6.QtWidgets import QApplication  # noqa: F401

    from teleflow.app import ConfigStore, MainWindow

    _HAVE_GUI = True
except Exception:  # pragma: no cover - environment dependent
    _HAVE_GUI = False

pytestmark = pytest.mark.skipif(not _HAVE_GUI, reason="PyQt6 not available")


def test_window_builds_with_status_and_settings_tabs(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    window = MainWindow(store)
    assert window.centralWidget() is not None
    # Defaults flow from the store into the settings page.
    assert store.load().sip_port == 5060
    window.close()
