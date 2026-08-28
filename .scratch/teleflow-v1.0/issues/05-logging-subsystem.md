# 05 — Logging subsystem

**What to build:** The Logging Subsystem captures SIP, media, and device-binding events and writes them to a persistent local file and a live scrolling window in the UI.

**Blocked by:** 02 — Audio Device Manager & device selection; 03 — SIP Core Service.

**Status:** ready-for-agent

- [ ] Registration, call, answer, hang-up, and media-error events appear in the live log window in real time.
- [ ] Device enumeration and device-switch events are logged.
- [ ] Logs are persisted to a local file and survive app restart.
- [ ] Log verbosity follows the configured log level.
