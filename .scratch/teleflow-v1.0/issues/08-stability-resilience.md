# 08 — Stability & resilience hardening

**What to build:** Stability & resilience hardening: the app runs 7×24 with no memory leak, no audio drop-out, and no drift across repeated calls, and auto-recovers from network drop and audio-device hotplug.

**Blocked by:** 04 — Audio Routing / Media Bridge; 05 — Logging subsystem; 06 — System tray & lifecycle.

**Status:** resolved (recoverable seams implemented + unit-tested via fakes; pure-runtime criteria need a soak on real hardware)

## What landed
- `src/teleflow/logging.py` — bounded **log rotation** (`max_bytes` + `backup_count`, default 1 MiB / 5 backups). Closes the 05/06 review follow-up (the log file grew unbounded); rotation is crash-safe (I/O errors swallowed).
- `src/teleflow/audio.py` — `EVENT_AUDIO_DEVICES_CHANGED` + `AudioDeviceManager.handle_hotplug()` re-enumerates (best-effort, never crashes the app) and announces the change.
- `src/teleflow/sip.py` — `SipBackend` gains `set_device_change_callback` + `recover()`; `FakeSipBackend` gains `receive_device_change` / `receive_network_down` / `recover` / `reroute` recording; `SipCoreService` gains `set_device_change_callback` (public delegation — no app poking `_backend`), `reroute_if_connected` (re-routes only when a call is `CONNECTED`), and `recover()` (backend recovery + re-emits `EVENT_SIP_STARTED`). `_dispatch` routes `network_down` → `recover()`.
- `src/teleflow/pjsua2_backend.py` — subclasses `pj.Endpoint` to hook `onAudioDevState` (→ device-change callback) and `onTransportState` DISCONNECTED (→ `network_down` handler); `set_device_change_callback` + `recover()` (re-applies the device route). Native-only paths are `# pragma: no cover`.
- `src/teleflow/app.py` — wires the backend device-change callback → `manager.handle_hotplug()` + `service.reroute_if_connected()`; logs hotplug events.

## Verification
- `tests/test_logging.py` — rotation keeps the live file bounded and caps backups at `backup_count`.
- `tests/test_audio.py` — `handle_hotplug` re-enumerates and emits `audio_devices_changed`.
- `tests/test_sip.py` — `reroute_if_connected` fires only while CONNECTED; `network_down` → `recover` (records + re-emits SIP started); device-change callback is invoked.
- `mypy src/teleflow`: clean. `pytest`: 40 passed.

## Acceptance criteria — recoverable seams vs. hardware-dependent
- [x] Log file is bounded and rotated (7×24 safe).
- [x] Audio-device hotplug triggers re-enumeration + re-route of a live call, crash-free.
- [x] Simulated network drop restores the SIP/audio path unattended (`network_down` → `recover` → re-route).
- [ ] Repeated call cycles show no measurable memory growth / no audio gaps or drift — **runtime-observable; needs a soak with a real ATA + virtual sound card.**
- [ ] Sustained 7×24 run remains stable — **runtime-observable; needs a soak.**
- Network recovery is scoped to UDP (pjsua2 auto-restores the UDP signaling path); re-registration for other transports is out of scope for V1.

## Follow-ups
- Ticket 07 (packaging) must bundle the native `_pjsua2.so` + its dynamic deps so the hardened app ships.
- A long-running soak harness (repeated INVITE/BYE + hotplug) would quantify the memory/drift criteria above.
