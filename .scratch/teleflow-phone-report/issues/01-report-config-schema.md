# 01 — Report config schema

**What to build:** TeleFlow persists the new phone-report settings (RPC toggles/port/token, desk-phone target, TTS voice, ffmpeg path) and loads them on launch; a missing or malformed file falls back to documented defaults, and unknown keys are ignored. This is the foundation every other ticket reads from.

**Blocked by:** None — can start immediately

**Status:** resolved

- [x] A fresh load with no config file yields the documented defaults: `rpc_enabled=True`, `rpc_port=8731`, `rpc_token=""`, `report_target=""`, `report_caller_id="TeleFlow"`, `report_hangup_on_eof=True`, `tts_voice="zh-CN-XiaoxiaoNeural"`, `ffmpeg_path=""`.
- [x] Saving a `Settings` record and loading it back yields an identical record (round-trip) including all new fields.
- [x] Unknown keys in the stored file are ignored; absent keys fall back to defaults (no exception on malformed JSON).
- [x] A unit test covers round-trip and default fallback.

## Notes
Implemented in `src/teleflow/config.py` (`Settings` gained 8 fields, all defaulted, appended after existing fields). Tests added in `tests/test_config.py`: `test_phone_report_fields_default_on_fresh_file`, `test_phone_report_fields_round_trip`, `test_old_file_without_phone_report_fields_uses_defaults`. All 11 config tests green.
