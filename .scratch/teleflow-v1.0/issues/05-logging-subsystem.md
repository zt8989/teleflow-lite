# 05 — Logging subsystem

**What to build:** The Logging Subsystem captures SIP, media, and device-binding events and writes them to a persistent local file and a live scrolling window in the UI.

**Blocked by:** 02 — Audio Device Manager & device selection; 03 — SIP Core Service.

**Status:** resolved

- [x] Registration, call, answer, hang-up, and media-error events appear in the live log window in real time.
- [x] Device enumeration and device-switch events are logged.
- [x] Logs are persisted to a local file and survive app restart.
- [x] Log verbosity follows the configured log level (applied at launch and on runtime change in the settings UI).
