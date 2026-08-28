"""Tests for the audio routing / media bridge (ticket 04).

The bridge is exercised with a fake ``AudioDeviceController`` so the two-way
routing and the no-recording / no-DSP red lines are verified without the native
pjsua2 library or any hardware.
"""

from __future__ import annotations

from teleflow.media import AudioDeviceController, AudioRoute, MediaBridge


class FakeAudioDeviceController:
    def __init__(self) -> None:
        self.methods: list[str] = []
        self.playback: str | None = None
        self.capture: str | None = None

    def set_playback_device(self, device_id: str) -> None:
        self.methods.append("set_playback_device")
        self.playback = device_id

    def set_capture_device(self, device_id: str) -> None:
        self.methods.append("set_capture_device")
        self.capture = device_id


def test_apply_routes_downstream_and_upstream() -> None:
    controller = FakeAudioDeviceController()
    MediaBridge(controller).apply(AudioRoute("vb-cable", "blackhole"))
    assert controller.playback == "vb-cable"
    assert controller.capture == "blackhole"


def test_apply_performs_no_recording_or_dsp() -> None:
    # The bridge may only ever select devices. Any other method (a recorder, a
    # DSP transform) would break the ticket-04 red line, so assert the only
    # methods the controller ever sees are the two device selectors.
    controller = FakeAudioDeviceController()
    MediaBridge(controller).apply(AudioRoute("0", "1"))
    assert set(controller.methods) == {"set_playback_device", "set_capture_device"}


def test_reapply_reroutes_live() -> None:
    controller = FakeAudioDeviceController()
    bridge = MediaBridge(controller)
    bridge.apply(AudioRoute("vb-cable", "blackhole"))
    bridge.apply(AudioRoute("hw:0,0", "hw:0,0"))
    assert controller.playback == "hw:0,0"
    assert controller.capture == "hw:0,0"
    assert controller.methods.count("set_playback_device") == 2


def test_apply_per_leg_routes_only_that_direction() -> None:
    # Independent device selection: switching only the speaker must not disturb
    # the already-selected capture device, and vice versa.
    controller = FakeAudioDeviceController()
    bridge = MediaBridge(controller)
    bridge.apply(AudioRoute("vb-cable", "blackhole"))
    bridge.apply_playback("hw:0,0")
    assert controller.playback == "hw:0,0"
    assert controller.capture == "blackhole"  # unchanged
    bridge.apply_capture("hw:1,0")
    assert controller.capture == "hw:1,0"
    assert controller.playback == "hw:0,0"  # unchanged
