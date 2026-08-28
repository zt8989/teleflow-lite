# 05 — RPC control channel

**What to build:** TeleFlow exposes a local HTTP control channel so an external hook can POST report text and have the machine dial the desk phone and play it. Token-authenticated, bound to `127.0.0.1` only, with a status probe. The report is invoked on the Qt main thread to avoid pjsua2 threading violations.

**Blocked by:** 01 — report-config-schema, 04 — report-controller-state-machine

**Status:** resolved

- [ ] `POST /v1/report` with `Authorization: Bearer <token>` and `{ "text": ... }` triggers the controller report flow and returns `202 { "report_id": ... }`.
- [ ] Missing/invalid token → `401`; SIP down / no target / both `text` and `audio_path` absent → `400` with a readable `error`; a second concurrent report → `409`.
- [ ] `audio_path` skips TTS; `voice` / `target` / `caller_id` override config per request.
- [ ] `GET /v1/status` returns rpc/sip/gateway/call/report state plus current `tts_voice` and `ffmpeg_path`.
- [ ] Server binds only `127.0.0.1:<rpc_port>`; the report is dispatched on the Qt main thread. Tested with a real local HTTP server on a free port plus fakes.
