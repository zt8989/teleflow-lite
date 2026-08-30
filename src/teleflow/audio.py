"""Audio device abstraction and selection (ticket 02).

The manager is deliberately backend-agnostic: it talks to an ``AudioBackend``
protocol so the real PortAudio/pjsua2 enumerator and a fake (used in tests and
when pjsua2 is unavailable) are interchangeable. That seam is what lets the
spec's "no real hardware" testing strategy work. The manager owns device
selection and persists it through the injected ``ConfigStore``; it performs no
audio I/O itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from teleflow.config import ConfigStore, Settings


# --- Windows WMME / pjsua2 device-name fix -------------------------------
# pjsip's WMME backend reads device names as raw ANSI bytes (GBK/cp936 on a
# zh-CN machine). pjsua2's SWIG wrapper decodes those bytes as UTF-8 with
# ``surrogateescape``, so the original bytes survive in ``info.name`` as lone
# surrogate code points (e.g. ``"\udcc1\udca2..."``). Re-encoding with
# ``surrogateescape`` recovers the exact ANSI bytes; decoding them with the
# system ANSI code page (``mbcs`` = the ACP, literally GBK on zh-CN) yields the
# correct name. This is pure in-process table decoding and is unaffected by the
# native library or the console/OEM codepage.


def _needs_recovery(name: str) -> bool:
    """True when ``name`` still carries the wrapper's surrogate-escaped bytes."""
    return any(0xDC00 <= ord(ch) <= 0xDFFF for ch in name)


def _recover_wmme_names(names: list[str]) -> list[str]:
    """Repair a batch of pjsua2 WMME names mangled by the SWIG wrapper."""
    result = list(names)
    for idx, name in enumerate(names):
        if _needs_recovery(name):
            try:
                result[idx] = name.encode("utf-8", "surrogateescape").decode("mbcs")
            except UnicodeError:
                pass  # keep the escaped name rather than dropping the device
    return result


class DeviceKind(str, Enum):
    PHYSICAL = "physical"
    VIRTUAL = "virtual"


@dataclass(frozen=True)
class AudioDevice:
    """A single audio endpoint exposed by a backend."""

    id: str
    name: str
    kind: DeviceKind
    supports_playback: bool
    supports_capture: bool


@runtime_checkable
class AudioBackend(Protocol):
    """Any source of an audio-device list."""

    def enumerate(self) -> list[AudioDevice]: ...


class FakeAudioBackend:
    """Deterministic device list for tests and headless runs.

    Mirrors the kind of devices TeleFlow expects to see: a physical headset plus
    the two common virtual sound cards (VB-Cable on Windows, BlackHole on macOS).
    Tests may append to ``devices`` to simulate hotplug before calling refresh.
    """

    def __init__(self, devices: list[AudioDevice] | None = None) -> None:
        self.devices = list(devices) if devices is not None else self._defaults()

    @staticmethod
    def _defaults() -> list[AudioDevice]:
        return [
            AudioDevice("hw:0,0", "Built-in Headset", DeviceKind.PHYSICAL, True, True),
            AudioDevice("vb-cable", "VB-Cable", DeviceKind.VIRTUAL, True, True),
            AudioDevice("blackhole", "BlackHole", DeviceKind.VIRTUAL, True, True),
        ]

    def enumerate(self) -> list[AudioDevice]:
        return list(self.devices)


class PortAudioBackend:
    """Real device enumeration via pjsua2 / PortAudio.

    pjsua2 is imported lazily so this module stays importable (and testable)
    without the native library installed. The library is initialised only as far
    as needed to read device info; the SIP UA (ticket 03) will own the full
    endpoint lifecycle.
    """

    def enumerate(self) -> list[AudioDevice]:
        try:
            import pjsua2 as pj
        except ImportError as exc:  # pragma: no cover - exercised only with pjsua2
            raise RuntimeError(
                "pjsua2 is required for real audio device enumeration"
            ) from exc

        # Share the single process-wide Endpoint with the SIP backend: pjsua2
        # aborts if a second ``pj.Endpoint()`` is constructed. The library state
        # is an int in 2.17 (0 = not created, 1 = created, 2 = initialized); only
        # create/init what the SIP backend has not already initialized.
        from teleflow.pjsua2_backend import _ep_config, get_shared_endpoint

        ep = get_shared_endpoint(pj)
        if ep.libGetState() == 0:  # pragma: no cover - needs lib
            ep.libCreate()
        if ep.libGetState() < 2:  # pragma: no cover - needs lib
            ep.libInit(
                _ep_config(
                    pj,
                    log_file=str(Path.home() / ".config" / "teleflow" / "pjsua2.log"),
                )
            )

        manager = ep.audDevManager()
        count = manager.getDevCount()  # pragma: no cover - needs lib

        # pjsua2 returns the raw device-name bytes as UTF-8 surrogate escapes; we
        # recover the correct Unicode name in-process (see module doc).
        infos = [manager.getDevInfo(index) for index in range(count)]  # pragma: no cover - needs lib
        names = _recover_wmme_names([info.name for info in infos])

        devices: list[AudioDevice] = []
        for index in range(count):  # pragma: no cover - needs lib
            info = infos[index]
            name = names[index]
            upper = name.upper()
            kind = (
                DeviceKind.VIRTUAL
                if any(token in upper for token in ("CABLE", "BLACKHOLE", "VIRTUAL"))
                else DeviceKind.PHYSICAL
            )
            devices.append(
                AudioDevice(
                    id=str(index),
                    name=name,
                    kind=kind,
                    supports_playback=info.outputCount > 0,
                    supports_capture=info.inputCount > 0,
                )
            )
        return devices


# Domain events emitted by the device manager (mirrors teleflow.sip EVENT_*).
EVENT_DEVICES_ENUMERATED = "devices_enumerated"
EVENT_DEVICE_SELECTED = "device_selected"
EVENT_PRESET_APPLIED = "preset_applied"
EVENT_AUDIO_DEVICES_CHANGED = "audio_devices_changed"

# TeleFlow hard rule: an empty / "-1" / -1 device id is never valid.
_INVALID_IDS = {"", "-1", -1, None}


class AudioDeviceManager:
    """Enumerates devices, exposes independent playback/capture selection, and
    persists that selection through the ``ConfigStore``.

    Selection is validated but not checked against the live enumeration, so a
    persisted id that is temporarily absent (device unplugged) still round-trips.
    """

    def __init__(self, backend: AudioBackend, store: ConfigStore) -> None:
        self._backend = backend
        self.store = store
        self._subscribers: dict[str, list[Callable[..., None]]] = {}
        self._devices: list[AudioDevice] = []
        self.refresh()

    def on(self, event: str, callback: Callable[..., None]) -> None:
        self._subscribers.setdefault(event, []).append(callback)

    def _emit(self, event: str, *args: object) -> None:
        for callback in self._subscribers.get(event, []):
            callback(*args)

    def refresh(self) -> None:
        self._devices = self._backend.enumerate()
        self._emit(EVENT_DEVICES_ENUMERATED, len(self._devices))

    def handle_hotplug(self) -> None:
        """React to an audio-device hotplug (plug/unplug).

        Re-enumerates the device list (best-effort — a transient enumeration
        failure must not crash the app) and announces the change so listeners
        (e.g. the SIP service) can re-route a live call.
        """
        try:
            self.refresh()
        except (OSError, RuntimeError):
            # Enumeration can hiccup on hotplug (e.g. native lib mid-state); the
            # change is still announced so a live call re-routes onto whatever
            # devices remain, and the app stays up.
            pass
        self._emit(EVENT_AUDIO_DEVICES_CHANGED)

    @property
    def devices(self) -> list[AudioDevice]:
        return list(self._devices)

    def playback_devices(self) -> list[AudioDevice]:
        return [d for d in self._devices if d.supports_playback]

    def capture_devices(self) -> list[AudioDevice]:
        return [d for d in self._devices if d.supports_capture]

    def current_selection(self) -> tuple[str, str]:
        settings = self.store.load()
        return (settings.playback_device_id, settings.capture_device_id)

    def set_selection(self, playback_id: str | None, capture_id: str | None) -> None:
        # The playback (downstream) device is mandatory — TeleFlow is no use with
        # nowhere to send call audio. The capture (upstream) device is optional:
        # an empty / "-1" / None id selects one-way (downstream only) operation,
        # matching MicroSIP, where the microphone is only opened when an input
        # device is actually selected. We normalise that to "" rather than
        # rejecting it, so the one-way choice persists through ConfigStore.
        if playback_id in _INVALID_IDS:
            raise ValueError("playback device selection must not be null or -1")
        normalised_capture = "" if capture_id in _INVALID_IDS else str(capture_id)
        settings = self.store.load()
        settings.playback_device_id = str(playback_id)
        settings.capture_device_id = normalised_capture
        self.store.save(settings)
        self._emit(EVENT_DEVICE_SELECTED, settings.playback_device_id, settings.capture_device_id)

    def apply_preset(self, preset: str) -> tuple[str, str]:
        if preset not in ("debug", "production"):
            raise ValueError(f"unknown preset: {preset!r}")
        if preset == "debug":
            # Debug mode is two-way through the physical headset: playback and
            # capture both on a real device, so you can actually talk on the phone.
            playback = next(
                (d for d in self._devices if d.kind is DeviceKind.PHYSICAL and d.supports_playback),
                None,
            )
            capture = next(
                (d for d in self._devices if d.kind is DeviceKind.PHYSICAL and d.supports_capture),
                None,
            )
            if playback is None or capture is None:
                raise ValueError(f"no physical device available for preset {preset!r}")
            self.set_selection(playback.id, capture.id)
            self._emit(EVENT_PRESET_APPLIED, preset)
            return (playback.id, capture.id)
        # Production mode is the "feed a virtual cable, another app consumes it as
        # a mic" relay (e.g. landline -> ATA -> FreeSWITCH -> TeleFlow -> VB-Cable
        # -> third-party app). Playback goes to the virtual device; capture is left
        # empty (one-way) so the OS never opens a microphone endpoint — matching
        # MicroSIP, where capture is only opened when an input device is selected.
        playback = next(
            (d for d in self._devices if d.kind is DeviceKind.VIRTUAL and d.supports_playback),
            None,
        )
        if playback is None:
            raise ValueError("no virtual playback device available for preset 'production'")
        self.set_selection(playback.id, None)
        self._emit(EVENT_PRESET_APPLIED, preset)
        return (playback.id, "")
