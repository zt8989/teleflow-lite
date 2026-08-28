# 01 — App shell & Config Store

**What to build:** A runnable PyQt6 desktop app that opens to a window containing a status panel and a settings page, plus a Config Store module that loads and saves the full settings record and applies it on launch. This is the foundation every other ticket builds on.

**Blocked by:** None — can start immediately.

**Status:** resolved

- [ ] App launches to a window with a visible status panel and a reachable settings page.
- [ ] Saving settings (SIP port, playback device id, capture device id, autostart, start-minimized, log level) and restarting the app reloads the same values.
- [ ] The settings record round-trips through the Config Store without loss or type coercion.
- [ ] The SIP listen port shown in settings defaults to 5060.
