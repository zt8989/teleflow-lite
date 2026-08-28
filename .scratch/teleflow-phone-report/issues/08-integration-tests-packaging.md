# 08 — Integration tests & packaging deps

**What to build:** End-to-end coverage of the report path using fakes plus a real local HTTP server, a red-line assertion that no call audio is ever recorded, and declaration of edge-tts / ffmpeg as runtime dependencies in packaging.

**Blocked by:** 05 — rpc-control-channel, 06 — ui-settings-logs

**Status:** resolved

- [x] An integration test drives a hook-like `POST /v1/report` (text) → TTS fake → outbound call (fake) → answer → playback → EOF → hangup, asserting state transitions and `EVENT_REPORT_*` events.
- [x] A red-line test asserts the report path never captures/records any call audio and inserts no recorder/DSP stage (playing a synthesized/provided file is allowed; recording a call is not).
- [x] The ffmpeg-missing path returns a clear error and does not crash the app.
- [x] edge-tts and the ffmpeg dependency (or the PATH/auto-discovery expectation) are declared in the project dependencies and macOS packaging notes.
