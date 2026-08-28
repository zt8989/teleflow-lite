# 03 — SIP backend report extension

**What to build:** The SIP backend can place an outbound "report" call and, once the desk phone answers, play a provided WAV one-way into the call (no microphone bridge), hanging up when playback ends. The scripted fake exposes hooks to drive this entire flow without pjsua2 or hardware.

**Blocked by:** None — can start immediately

**Status:** resolved

- [x] `place_report_call(target, wav_path)` initiates an outbound call tagged as a report call.
- [x] When media becomes active for a report call, the backend does NOT bridge to devices; it signals `report_connected` once, and `play_file_to_call(call_id, wav_path)` plays the file one-way into the call. Ordinary inbound calls still bridge two-way as before.
- [x] Playback end-of-file fires `report_eof` (via a pjsua2 `AudioMediaPlayer` EOF callback) so the service can hang up.
- [x] `FakeSipBackend` implements `place_report_call` and the scripted `receive_report_connected` / `receive_report_playback_done` hooks so the controller can be driven end-to-end.
- [x] The real pjsua2 playback / EOF paths are covered by the native-only guard (not asserted in CI).

## Notes
Added `place_report_call` / `play_file_to_call` to the `SipBackend` protocol and both backends. `Pjsua2Backend.onCallMediaState` now branches on a per-Call `_is_report` flag: report calls fire `report_connected` (once) instead of bridging; normal calls bridge as before. `play_file_to_call` creates a `pjsua2.AudioMediaPlayer`, transmits one-way into the call, and registers `_make_report_eof_callback` → `report_eof`. `FakeSipBackend` records `report_calls`/`report_played` and exposes `receive_report_connected`/`receive_report_playback_done`. Tests: `tests/test_sip.py::test_fake_place_report_call_is_recorded`, `test_fake_report_lifecycle_fires_handler`. Full suite: 55 passed; mypy clean. (The service-side handling of `report_connected`/`report_eof` + `report_state` is ticket 04.)
