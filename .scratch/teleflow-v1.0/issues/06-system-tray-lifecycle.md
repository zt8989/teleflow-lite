# 06 — System tray & lifecycle

**What to build:** System Tray & lifecycle: the app minimizes to the system tray, exposes a tray menu (start service, stop service, show window, quit), runs at low power in the background, and honors autostart and start-minimized from the Config Store.

**Blocked by:** 01 — App shell & Config Store; 03 — SIP Core Service.

**Status:** ready-for-agent

- [ ] Closing the window hides the app to the tray rather than quitting.
- [ ] Tray menu start/stop toggles the SIP service; show window restores the UI; quit exits the app.
- [ ] Autostart and start-minimized settings are honored on launch.
- [ ] App sustains background residency with low CPU/RAM.
