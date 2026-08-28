# 06 — UI / settings / logs

**What to build:** The user can configure RPC, the desk-phone target, TTS voice, and ffmpeg path in Settings; watch live report status on the dashboard; trigger a test report from the tray; and follow `[REPORT]`/`[TTS]`/`[FFMPEG]` steps in the live log.

**Blocked by:** 01 — report-config-schema, 04 — report-controller-state-machine, 05 — rpc-control-channel

**Status:** resolved

- [x] Settings modal has an RPC section (enable / port / token view+reset) and a Report section (target URI, caller id, TTS voice, ffmpeg path + a "test ffmpeg" indicator), all persisted via ConfigStore.
- [x] The dashboard shows a report status card reflecting `report_state` (idle / dialing / playing / completed / failed).
- [x] The tray menu has a "测试汇报" action that dials and plays a default test text.
- [x] Report / TTS / ffmpeg steps appear in the live log with distinct categories, separate from the existing `[SIP]`/`[CALL]`/`[MEDIA]`/`[AUDIO]`/`[ERROR]`.
