# 06 — System tray & lifecycle

**What to build:** System Tray & lifecycle: the app minimizes to the system tray, exposes a tray menu (start service, stop service, show window, quit), runs at low power in the background, and honors autostart and start-minimized from the Config Store.

**Blocked by:** 01 — App shell & Config Store; 03 — SIP Core Service.

**Status:** resolved

- [x] Closing the window hides the app to the tray rather than quitting.
- [x] Tray menu start/stop toggles the SIP service; show window restores the UI; quit exits the app.
- [x] Autostart and start-minimized settings are honored on launch (autostart toggled both on and off via macOS LaunchAgent).
- [x] App sustains background residency with low CPU/RAM (UI log view capped at 2000 lines; file logging is append-only and failure-swallowed).

> Follow-up (tracked in ticket 08 — stability/resilience): log *file* rotation is not yet implemented, so the on-disk log grows unbounded over very long uptimes.
