# 02 — 服务层采用 ResolvedConfig

**What to build:** `SipCoreService` 的 ffmpeg/报告相关逻辑改走
`ResolvedConfig`,不再自行调 `locate_ffmpeg`/`resolve_report_target`。

**Blocked by:** 01

**Status:** done

- [ ] `_log_ffmpeg_readiness` 用 `config.ffmpeg_bin`
- [ ] `_tts_backend` 用 `config.ffmpeg_bin` 构建 EdgeTtsBackend
- [ ] `resolve_report_target` → `ResolvedConfig.report_target`
- [ ] 214 测试全过,行为不变
