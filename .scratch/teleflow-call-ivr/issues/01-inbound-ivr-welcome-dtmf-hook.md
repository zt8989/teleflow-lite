# 01 — 呼入 IVR：欢迎语 + 每数字键独立播报 / Hook + 挂机 hook 带末次按键

**What to build:** 在 `teleflow-sip-hooks` 的呼入生命周期（`EVENT_CALL_CONNECTED` / `EVENT_CALL_ENDED`）之上，叠加一段简单 IVR。呼入自动接通后：先单向播放一段**欢迎语**，再按 `1-9-0` 顺序逐项播放每个数字键**各自**的配置文字（某键文字为空则跳过）；随后监听来电者的 DTMF 按键，**首个**按下的数字键触发**该键对应**的 `hook` 命令（带 `{call_id}` / `{digit}`），并停止监听后续按键；挂机时现有 `on_hook_cmd` 额外获得 `{last_digit}` 替换（未按键则为空串）。

`1234567890` 指数字按键 1-9-0（全部按键），**不是**分机号。配置为「每数字键」独立：每个键有各自的 `text` 与 `hook`。

复用既有能力，避免重复造轮子：
- TTS：复用 `teleflow/tts.py` 的 `TtsBackend` 协议与 `clean_markdown`，真实实现 `EdgeTtsBackend`（edge-tts→ffmpeg 8k mono wav），测试用 `FakeTtsBackend`。
- 单向播放：复用 `SipBackend.play_file_to_call` / `Pjsua2Backend.play_file_to_call` 的"文件→通话、不接麦克风"机制；但需一个**不带 EOF 自动挂断**的变体（report call 当前会在 EOF 调 `hangup`，IVR 不应在播完菜单后挂断）。
- Hook 执行：复用 `teleflow/hooks.py` 的 `HookRunner` 协议与 `SubprocessHookRunner`（后台线程、替换占位符、记日志、吞异常），`attach_hooks` 增加 DTMF 订阅与 `{last_digit}` 注入。
- DTMF（新增原生能力）：在 `pjsua2_backend._make_classes` 的 `Call` 子类实现 `onDtmfDigit(digit)`，转发 `backend._handler("dtmf", {"call_id": ..., "digit": ...})`；`FakeSipBackend` 加 `receive_dtmf(call_id, digit)` 测试钩子；`SipCoreService._dispatch` 处理 `dtmf`，维护每通呼入的 `last_digit` 并在 `EVENT_CALL_ENDED` 时把它代入 `on_hook_cmd`。

配置（`ConfigStore` / `Settings`，与 `off_hook_cmd` / `on_hook_cmd` 同模式）：
- `ivr_enabled: bool = True` — IVR 总开关；关闭即回到纯音频路由器（两路桥接、不播报、不监听按键）。
- `ivr_welcome: str = ""` — 欢迎语；空则跳过。
- `ivr_digit_text: dict[str, str] = {}` — 每数字键的播报词，键为 `"1".."9"`、`"0"`；缺键或值为空串 → 菜单中跳过不播。
- `ivr_digit_hook: dict[str, str] = {}` — 每数字键对应的命令，键同上；缺键或值为空串 → 该键被按下时不执行命令（仍记录 `last_digit` 并停止监听）。
- `on_hook_cmd: str = ""` — 挂机命令（已有字段），新增 `{last_digit}` 占位符。

**Blocked by:** None — 构建块（`teleflow-sip-hooks`、`teleflow-phone-report` 的 TTS/单向播放）已随现版本交付；4 个 design decisions 已于 2026-08-29 与用户确认（见下）。

**Status:** done

- [ ] `ivr_enabled`（默认 True）为真时，呼入自动接通即进入 IVR：呼入 Call 标记 `_is_ivr`，`onCallMediaState` 跳过 `mic → call` 桥接（沿用 report call 的"不接麦克风"思路）；`ivr_enabled` 为假则行为与现版一致（两路桥接、不播报、不监听按键）。
- [ ] 呼入 IVR 接通后，先单向播放 `ivr_welcome` 欢迎语（TTS 合成 → 播放）；`ivr_welcome` 为空则不播。
- [ ] 按 `1-9-0` 顺序遍历 `ivr_digit_text`：对每个非空文字单向播放；缺键或空串的键**跳过不播**（对应"如果空就不播报"），日志应记录跳过的键。
- [ ] **语音缓存**：欢迎语与每个键的 `text` 首次合成后按 `hash(clean_markdown(text) + voice)` 缓存 wav（落在现有 reports 缓存目录）；同文本再次呼入直接复用缓存，不重复合成；文本/音色变化时哈希不同、自动重新生成。
- [ ] 来电者按键（DTMF）被检测到：`Call.onDtmfDigit` 在原生后端实现，`FakeSipBackend.receive_dtmf` 可在无硬件下端到端测试。
- [ ] **首次按键**触发**该键**的 `ivr_digit_hook[digit]`（非空才执行），`{call_id}` 与 `{digit}` 被正确替换；触发后**停止监听按键**（忽略后续按键）；该键无 `hook` 配置则不执行命令；失败不阻塞通话、错误记入日志。
- [ ] 服务维护每通呼入的"末次按键"（= 首次按键）；`EVENT_CALL_ENDED` 触发 `on_hook_cmd` 时，`{last_digit}` 被替换（未按键则为空串），`{call_id}` 仍可用。
- [ ] `Settings` 新增 `ivr_enabled` / `ivr_welcome` / `ivr_digit_text` / `ivr_digit_hook` 并随 `ConfigStore` 持久化（dict 字段 JSON 往返正确）；设置弹窗提供"启用 IVR"开关、`ivr_welcome` 输入框，以及 1-9-0 每个键的 `text` / `hook` 输入。
- [ ] 单元覆盖：欢迎语播放、`1-9-0` 菜单顺序、空文字键跳过、语音缓存命中/失效、DTMF→该键 `hook` 且后续按键被忽略、`{last_digit}` 注入挂机 hook；用 `FakeSipBackend` + `FakeTtsBackend` + `FakeHookRunner` 在 CI 无硬件、无 pjsua2、无网络下验证。
- [ ] 红线断言：IVR 路径不录制/不写通话 WAV、不对通话做 DSP（播放合成文件与读取 DTMF 信令均为允许例外）。

## Decisions (confirmed 2026-08-29)

1. **抑制麦克风桥接**：IVR 期间不桥接麦克风；呼入 Call 标记 `_is_ivr`。保留 `ivr_enabled` 总开关（默认 True，关闭即回到纯路由器）。
2. **设置文字 / hook 为「每数字键」独立配置**（非全局单一文字）：`1234567890` 指按键 1-9-0；每个键各有 `text`（播报词）与 `hook`（命令）。空文字键跳过不播；无 `hook` 配置的键按下时不执行命令。
3. **文字全部可配置 + 语音缓存**：欢迎语与每个键的 `text` 均可配置；首次 TTS 后按"文本+音色"哈希缓存 wav，变化才重新生成。
4. **首次按键即触发、随后停止监听**：第一个 DTMF 按键触发**该键**的 `hook`，之后忽略后续按键；`last_digit` 记为该首次按键，挂机 hook 的 `{last_digit}` 即此值。

## Notes

- 这不是 `teleflow-phone-report` 的外呼汇报：report call 在 EOF 自动挂断且**不**触发 `EVENT_CALL_ENDED`/挂机 hook，因此"挂机 hook 带末次按键"应落在**呼入**生命周期（`CALL_ENDED`），与现有 `on_hook_cmd` 一致。
- DTMF 是本次净新增能力：全仓当前无 `onDtmfDigit` 实现，需同时补真实后端回调、`FakeSipBackend` 钩子与 `SipCoreService` 的 `last_digit` 状态/事件常量。
- 菜单文字在接通后**预先播报**（作为电话菜单），按键只触发该键 `hook`、不重播该键文字；如需"按键后重播该键文字"属后续扩展。
