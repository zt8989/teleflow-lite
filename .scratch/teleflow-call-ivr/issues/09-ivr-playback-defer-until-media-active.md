# 09: 呼入 IVR 欢迎/菜单语音不播报（媒体 ACTIVE 前播放被丢弃）

**What to build:** 修复真实呼入（pjsua2 后端）时 IVR 欢迎语与数字菜单完全听不到的问题。根因：`_maybe_start_ivr` 在 `answer` 的同一时刻就调用 `play_file_to_call`，而此时 pjsua2 的音频媒体尚未进入 `PJSUA_CALL_MEDIA_ACTIVE`，`play_file_to_call` 返回 `False`（原 `_ivr_play_next` 先 `pop` 再播放，等于把队首条目直接丢弃）；又因为 `playback_done` 不会触发，整条菜单链随之中断，于是「一句都听不到」。修复：把 IVR 播放推迟到后端真正信号媒体可用时再开始，且播放失败时把条目放回队首（不丢），避免媒体未就绪窗口吞掉欢迎/菜单。

**Blocked by:** 05 — 呼入 IVR 编排, 04 — 后端 DTMF 与播放

**Status:** done

- [ ] `pjsua2_backend.onCallMediaState` 的 `_is_ivr` 分支：媒体 ACTIVE 时向 service 派发新事件 `call_media_active`（之前该分支仅 `return`，什么都不做）。事件常量 `EVENT_CALL_MEDIA_ACTIVE = "call_media_active"` 加在 `sip.py`。
- [ ] `SipCoreService` 新增 `_on_call_media_active(call_id)`：`_ivr_active` 且 `call_id == _ivr_call_id` 且尚未开始播放时，调用 `_ivr_play_next()` 重试。用 `_ivr_started` 标志保证只触发一次。
- [ ] `_ivr_play_next` 改为「先 `pop` 队首；若 `play_file_to_call` 返回 `False` 则把该条目 `insert(0)` 放回队首」，从而媒体未就绪时丢掉的是「再试一次的机会」而非菜单条目本身。`_ivr_started` 在成功播放首个条目后置 True。
- [ ] `replay_ivr_menu` 重置 `_ivr_started = False`，使重播后若再遇媒体窗口也能正常重试（当前重播发生在已建立通话，媒体本就 ACTIVE，主要为正交健壮性）。
- [ ] 单测 `test_ivr_defers_playback_until_media_active`：用一个「媒体未就绪时 `play_file_to_call` 返回 False」的 Spy 后端，验证 `receive_invite` 后队列完整保留、无播放；派发 `call_media_active` 后欢迎+菜单按序播放并进入监听；额外二次 `call_media_active` 不重复播放。

## Comments

- 2026-08-29: 用户反馈「呼入之后没有听到欢迎声音」。排查确认 `ivr_welcome='您好，欢迎拨打甜甜热线！'` 已配置（并非漏配欢迎语），故根因是媒体时序：真实后端在 answer 时媒体未 ACTIVE，`play_file_to_call` 返回 False，旧代码 `pop` 即丢失，且 `onCallMediaState` 的 IVR 分支不触发任何播放，导致整条欢迎+菜单静默。
- 2026-08-29: 已修复并加单测。关键设计点：原 `_ivr_play_next` 的「先 pop 再播放」依赖 FakeSipBackend 在 `play_file_to_call` 内**同步** fire `playback_done` 推进链式播放；若改成「先 peek 不 pop」会在假后端里造成无限递归（playback_done 在返回前同步触发、队首始终未弹出）。故采用「pop 后失败时放回队首」的折中，既兼容假后端同步链，又能在真后端媒体未就绪时保住条目。
