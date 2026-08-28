# 02 — Audio Device Manager & device selection

**What to build:** The Audio Device Manager enumerates every system audio device via PortAudio on launch and on a refresh action, separating playback (Speaker) from capture (Microphone), and the settings UI presents two independent dropdowns populated from that enumeration plus a refresh button and debug/production preset buttons. Selection persists via the Config Store. Null devices and `audioDevId = -1` are never allowed.

**Blocked by:** 01 — App shell & Config Store.

**Status:** ready-for-agent

- [ ] On launch and on refresh, physical and virtual sound cards (e.g. VB-Cable, BlackHole) appear in both the playback and capture dropdowns.
- [ ] Playback and capture devices can be chosen independently of each other.
- [ ] A selected device persists and is restored on next launch.
- [ ] Refresh picks up devices added or removed after launch.
- [ ] The manager rejects any null device or `audioDevId = -1` selection.
- [ ] Debug preset sets headset speaker + headset mic; production preset sets virtual-sound-card in/out.
