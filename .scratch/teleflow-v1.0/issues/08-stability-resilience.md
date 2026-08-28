# 08 — Stability & resilience hardening

**What to build:** Stability & resilience hardening: the app runs 7×24 with no memory leak, no audio drop-out, and no drift across repeated calls, and auto-recovers from network drop and audio-device hotplug.

**Blocked by:** 04 — Audio Routing / Media Bridge; 05 — Logging subsystem; 06 — System tray & lifecycle.

**Status:** ready-for-agent

- [ ] Repeated call cycles show no measurable memory growth and no audio gaps or drift.
- [ ] A simulated network drop and recovery restores the SIP/audio path without manual intervention.
- [ ] Plugging or unplugging an audio device triggers recovery (re-enumeration + re-route or clean restart) without a crash.
- [ ] A sustained 7×24 run remains stable.
