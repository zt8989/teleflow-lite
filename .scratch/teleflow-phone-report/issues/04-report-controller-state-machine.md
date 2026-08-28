# 04 — Report controller state machine

**What to build:** `SipCoreService` turns a report request (text or ready-made wav) into the complete flow: validate (SIP up, target set, file/ffmpeg ready) → synthesize if needed → place the report call → on answer play the file → on playback end hang up → emit `EVENT_REPORT_*` and track a `ReportState`. Verifiable end-to-end using the fake SIP backend and fake TTS backend.

**Blocked by:** 01 — report-config-schema, 02 — tts-synthesis-layer, 03 — sip-backend-report-extension

**Status:** resolved

- [x] A report with text triggers TTS, then an outbound call to the configured target.
- [x] On simulated answer the controller requests file playback; on simulated EOF it hangs up and resets to idle.
- [x] Each step emits the matching `EVENT_REPORT_*` event; `report_state` reflects idle / dialing / playing / completed / failed.
- [x] Failure paths (SIP down, no target, TTS/ffmpeg failure, missing file) emit `EVENT_REPORT_FAILED(reason)` and reset to idle.
- [x] At most one report runs at a time (concurrency guard in `start_report` raises `ReportBusyError`).

## Notes
Added `ReportState` enum, `EVENT_REPORT_*` constants, `ReportBusyError` to `sip.py`; `SipCoreService` gained `tts` injection, `start_report(text, *, audio_path, voice, target, caller_id)`, `_on_report_connected` / `_on_report_eof` / `_fail_report`, and `report_state` / `report_in_progress` properties. `_dispatch` now handles `report_connected` / `report_eof`. Synthesizes via injected `TtsBackend` (or lazily builds `EdgeTtsBackend`); `audio_path` skips TTS. Tests: `tests/test_report_controller.py` (8 tests, all green). Full suite green; mypy clean. (Note: `caller_id` is accepted by `start_report`/RPC but not yet mapped onto the SIP From header — deferred; everything else applies.)
