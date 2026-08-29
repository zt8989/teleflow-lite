"""Audio routing / media bridge (ticket 04).

TeleFlow is a *pure* audio router: it takes an established RTP session and
connects its two directions to the user-selected devices — downstream decoded
audio to the playback device, upstream capture from the capture device. It
deliberately performs **no recording and no DSP** (no denoise / gain / mix /
transform). That absence is the red-line guarantee of ticket 04.

The bridge depends only on a tiny ``AudioDeviceController`` protocol — the
subset of pjsua2's ``audDevManager`` we touch. Isolating it behind a protocol
is what lets the routing policy be unit-tested with a fake controller, with no
native library and no hardware. The real pjsua2 adapter lives in
``pjsua2_backend.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AudioRoute:
    """The two device endpoints a call's audio is wired to."""

    playback_device_id: str
    capture_device_id: str


def capture_device_selected(device_id: str | None) -> bool:
    """True when a real capture (input) device is selected.

    An empty / "-1" / None id means one-way (downstream only): the microphone
    bridge must not be opened, matching MicroSIP, where the OS microphone prompt
    only appears when an input device is actually selected. Centralised here so
    the backend's startup route and the per-call media-state handler agree on the
    rule — and so the decision is unit-testable without the native pjsua2 lib.

    Defensive against ``-1`` arriving as either the int or the ``"-1"`` string
    (the audio layer normalises both to ``""`` before persisting, but the call
    sites may pass an un-normalised id).
    """

    return device_id is not None and str(device_id) not in ("", "-1")


@runtime_checkable
class AudioDeviceController(Protocol):
    """The minimal audio-device surface the bridge needs.

    Matching pjsua2's ``audDevManager`` so the real backend is a thin adapter.
    Note there is intentionally **no** recorder / transform method here — the
    bridge is only ever allowed to select devices.
    """

    def set_playback_device(self, device_id: str) -> None: ...
    def set_capture_device(self, device_id: str) -> None: ...


class MediaBridge:
    """Wires a call's audio to the selected devices, losslessly and two-way.

    The bridge never creates a recorder or applies any DSP — it only selects
    the playback and capture devices on the underlying controller. Calling
    ``apply`` again re-selects the devices, which (for pjsua2) re-routes a live
    call without a restart.
    """

    def __init__(self, controller: AudioDeviceController) -> None:
        self._controller = controller

    def apply(self, route: AudioRoute) -> None:
        # Two-way, lossless: downstream -> playback device, upstream -> capture
        # device. No extra AudioMedia (recorder / converter) is inserted, which
        # is exactly what keeps the path free of recording and DSP.
        self._controller.set_playback_device(route.playback_device_id)
        self._controller.set_capture_device(route.capture_device_id)

    def apply_playback(self, device_id: str) -> None:
        # Re-select only the downstream (playback) endpoint — used when the user
        # switches just the speaker mid-call.
        self._controller.set_playback_device(device_id)

    def apply_capture(self, device_id: str) -> None:
        # Re-select only the upstream (capture) endpoint — used when the user
        # switches just the microphone mid-call.
        self._controller.set_capture_device(device_id)
