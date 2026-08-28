# TeleFlow macOS packaging

Builds an unsigned `TeleFlow.app` and wraps it in a DMG via
`build_dmg.sh` (PyInstaller onedir + `hdiutil`). The app is left unsigned so it
launches for the building user; distributing to others requires an Apple
Developer signature + notarization (out of scope).

## Runtime dependencies not bundled

- **edge-tts** — a pip dependency, frozen into the app automatically.
- **ffmpeg** — an *external binary*, **not** a Python package and **not** bundled
  by this script. The phone-report feature uses it to transcode the synthesized
  mp3 into an 8 kHz mono WAV that pjsua2 can play.

  On the target machine, ffmpeg must be:
  1. available on `PATH` (e.g. `brew install ffmpeg`), **or**
  2. set explicitly via TeleFlow's `ffmpeg_path` setting (Settings → 电话汇报).

  If ffmpeg is missing, a report fails fast with a clear `FfmpegNotFound` error
  in the log; TeleFlow does not crash and the rest of the app keeps working.
