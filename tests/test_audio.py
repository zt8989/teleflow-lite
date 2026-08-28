"""TDD tests for the Audio Device Manager (ticket 02).

Written red (the module does not exist yet), then made green by
``src/teleflow/audio.py``. The manager is exercised through its public
interface against a ``FakeAudioBackend`` so no real sound hardware or pjsua2 is
required — this is the pre-agreed testing seam from the spec.
"""

import pytest

from teleflow.audio import AudioDevice, AudioDeviceManager, DeviceKind, FakeAudioBackend
from teleflow.config import ConfigStore


def _manager(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    return AudioDeviceManager(FakeAudioBackend(), store)


def test_enumeration_includes_physical_and_virtual_cards(tmp_path) -> None:
    mgr = _manager(tmp_path)
    names = {d.name for d in mgr.devices}
    assert "VB-Cable" in names
    assert "BlackHole" in names
    assert "Built-in Headset" in names


def test_playback_and_capture_split_by_capability(tmp_path) -> None:
    mgr = _manager(tmp_path)
    assert mgr.playback_devices()  # at least one playback-capable device
    assert mgr.capture_devices()   # at least one capture-capable device


def test_selection_persists_across_reload(tmp_path) -> None:
    mgr = _manager(tmp_path)
    mgr.set_selection("vb-cable", "blackhole")

    reloaded = AudioDeviceManager(FakeAudioBackend(), ConfigStore(tmp_path / "config.json"))
    assert reloaded.current_selection() == ("vb-cable", "blackhole")


def test_selection_of_null_or_negative_one_is_rejected(tmp_path) -> None:
    mgr = _manager(tmp_path)
    with pytest.raises(ValueError):
        mgr.set_selection(None, "blackhole")
    with pytest.raises(ValueError):
        mgr.set_selection("vb-cable", "-1")
    with pytest.raises(ValueError):
        mgr.set_selection("", "blackhole")
    with pytest.raises(ValueError):
        mgr.set_selection("vb-cable", -1)


def test_refresh_picks_up_devices_added_after_launch(tmp_path) -> None:
    backend = FakeAudioBackend()
    mgr = AudioDeviceManager(backend, ConfigStore(tmp_path / "config.json"))
    before = len(mgr.devices)

    backend.devices.append(AudioDevice("usb-mic", "USB Mic", DeviceKind.PHYSICAL, False, True))
    mgr.refresh()

    assert len(mgr.devices) == before + 1
    assert any(d.name == "USB Mic" for d in mgr.devices)


def test_debug_preset_selects_physical_headset(tmp_path) -> None:
    mgr = _manager(tmp_path)
    mgr.apply_preset("debug")
    assert mgr.current_selection() == ("hw:0,0", "hw:0,0")


def test_production_preset_selects_virtual_sound_card(tmp_path) -> None:
    mgr = _manager(tmp_path)
    mgr.apply_preset("production")
    assert mgr.current_selection() == ("vb-cable", "vb-cable")
