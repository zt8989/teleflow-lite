# 07 — Cross-platform packaging & permissions

**What to build:** Cross-platform packaging & permissions: produce a PyInstaller single-file Windows EXE and a macOS DMG; Windows runs without microphone-permission popups; macOS requests audio permission correctly on Intel and Apple Silicon and recognizes BlackHole without crashing or black-screening.

**Blocked by:** 04 — Audio Routing / Media Bridge; 06 — System tray & lifecycle.

**Status:** ready-for-agent

- [ ] A Windows EXE launches and runs the full flow standalone.
- [ ] A macOS DMG installs and runs the full flow on both Intel and Apple Silicon.
- [ ] Windows shows no intrusive mic-permission prompt during normal operation.
- [ ] macOS audio permission is requested correctly and the app stays stable (no crash, no black screen).
