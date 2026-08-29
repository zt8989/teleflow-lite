# 05: 呼入 IVR 编排（欢迎语 + 菜单 + 首键 + 挂机参数）

**What to build:** 呼入自动接通且 `ivr_enabled` 时，系统单向播欢迎语，再按 `1-9-0` 顺序播放各键配置文字（空键跳过），随后监听首个 DTMF 按键并触发对应事件后停止监听；挂机时挂机 hook 收到末次按键。这是把配置、缓存、后端能力串起来的核心编排。

**Blocked by:** 02 — IVR 配置 schema, 03 — TTS 语音缓存层, 04 — 原生后端 DTMF 回调与单向非挂断播放

**Status:** done

- [ ] `CALL_CONNECTED` 且 `ivr_enabled`：调用 `backend.mark_ivr(call_id)` 抑制麦克风桥接。
- [ ] 合成欢迎语 + 各键 `text` 为缓存 wav，按队列顺序播放（`playback_done` 事件驱动下一曲），空文字键跳过不播。
- [ ] 收到首个 `dtmf` 事件：emit `EVENT_IVR_DIGIT(call_id, digit)`，置"已触发"并停止监听后续按键。
- [ ] 维护 `last_digit`（= 首次按键）；`EVENT_CALL_ENDED` 事件携带 `last_digit`（未按键为空串）。
- [ ] `ivr_enabled=False` 时行为完全不变（两路桥接、不播报、不监听按键）。
- [ ] 用 `FakeSipBackend` + `FakeTtsBackend` 在 CI 无硬件、无 pjsua2、无网络下端到端验证：播放顺序、空键跳过、首键触发并停止、挂机带 `last_digit`。
