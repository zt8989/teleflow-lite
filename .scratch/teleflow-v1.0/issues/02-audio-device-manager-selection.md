# 02 — Audio Device Manager & device selection

**What to build:** The Audio Device Manager enumerates every system audio device via PortAudio on launch and on a refresh action, separating playback (Speaker) from capture (Microphone), and the settings UI presents two independent dropdowns populated from that enumeration plus a refresh button and debug/production preset buttons. Selection persists via the Config Store. Null devices and `audioDevId = -1` are never allowed.

**Blocked by:** 01 — App shell & Config Store.

**Status:** resolved

- [x] On launch and on refresh, physical and virtual sound cards (e.g. VB-Cable, BlackHole) appear in both the playback and capture dropdowns.
- [x] Playback and capture can be chosen independently.
- [x] A selected device persists and is restored on next launch.
- [x] Refresh picks up devices added or removed after launch.
- [x] The manager rejects any null device or `audioDevId = -1` selection.
- [x] Debug preset sets headset speaker + headset mic; production preset sets virtual-sound-card in/out.

## Implementation notes

Delivered in `src/teleflow/audio.py` (`AudioDeviceManager` + `FakeAudioBackend` + `PortAudioBackend`) and wired into `src/teleflow/app.py` (device dropdowns, refresh button, preset buttons). Built TDD red→green (`tests/test_audio.py`); offscreen GUI smoke test extended. Real device enumeration depends on `pjsua2`, installed in ticket 03 — until then `build_app` falls back to the fake backend (warned). 14 tests pass; mypy clean.
