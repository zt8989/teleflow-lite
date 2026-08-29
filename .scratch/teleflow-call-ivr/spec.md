# TeleFlow — 呼入 IVR（欢迎语 + 每数字键独立播报 / Hook）

- **Status:** `done`
- **Labels:** `ready-for-agent`
- **Source:** 用户需求：电话接通后先播欢迎语，再逐项播报每个数字键（1-9-0）各自的"设置文字"；听者按某个键后启动**该键**对应的 hook；某键文字为空则跳过不播；挂机 hook 携带"上次按键"。这是 `teleflow-phone-report` spec 中明确的 Out-of-Scope（IVR）能力，叠加在 `teleflow-sip-hooks` 的呼入生命周期之上。
- **Feature slug:** `teleflow-call-ivr`
- **前置：** `teleflow-sip-hooks`（摘机/挂机 hook 与可注入的 `HookRunner`）、`teleflow-phone-report`（TTS 合成层 `TtsBackend` 与"单向播放文件进通话"的 `play_file_to_call` 机制）、`sip-softphone`（呼入自动应答桥接）。

---

## Problem Statement

呼入自动应答后，当前只发生两件事：把通话桥接到用户声卡（纯音频路由器），并触发摘机/挂机 hook。用户希望呼入接通后能"主动播报"形成一段简单 IVR：

1. 先播一段**欢迎语**；
2. 再逐项播报每个数字键（1-9-0）**各自**的"设置文字"——每个键可单独配置一段播报词，作为电话菜单；某键文字为空则跳过不播；
3. 让听者用电话按键（DTMF）与系统交互——按下某个数字键后，启动**该键对应**的 hook（每个键可单独配置一条命令）；
4. **挂机时**挂机 hook 能拿到"上次按下的键"；
5. 文字为空（或无配置）的键不播报、不触发。

注：`1234567890` 指的是数字按键 1-9-0（全部按键），**不是**分机号 / 扩展号。

## Solution (sketch)

呼入 `EVENT_CALL_CONNECTED` 后，在保持现有 `off_hook_cmd` 行为的同时，依次：

1. 合成并**单向**播放全局"欢迎语"（`ivr_welcome`，TTS → 8k mono wav，可常驻内存）。
2. 按 `1-9-0` 顺序遍历配置的数字键：对配置了非空 `text` 的键，合成并单向播放该键文字；文字为空/未配置则跳过（对应"如果空就不播报"）。
3. 开启 **DTMF 监听**（pjsua2 `Call.onDtmfDigit`）；收到首个按键后，查找该键对应的 `hook` 命令（非空才执行，带 `{call_id}` / `{digit}`），随后**停止监听**后续按键。
4. `EVENT_CALL_ENDED` 时现有 `on_hook_cmd` 额外获得 `{last_digit}` 替换（未按键则为空串），其余行为不变。

## Decisions（已与用户确认，2026-08-29）

1. **抑制麦克风桥接**：IVR 播报期间不把麦克风桥接进通话。呼入 Call 标记 `_is_ivr`（沿用 report call 的"不接麦克风"思路）。另加 `ivr_enabled: bool = True` 总开关：关闭即回到纯音频路由器行为（两路桥接、不播报、不监听按键）。
2. **设置文字 / hook 为「每数字键」独立配置**：不是全局单一文字。`1234567890` 指按键 1-9-0；每个键可单独配置一段 `text`（播报词）与一条 `hook`（命令）。某键 `text` 为空/未配置 → 菜单中跳过不播；某键 `hook` 为空/未配置 → 按键时不执行命令（但仍记录 `last_digit` 并停止监听）。
3. **文字全部可配置 + 语音缓存**：欢迎语与每个键的 `text` 均可配置；首次 TTS 合成后按"清洗后文本 + 音色"缓存 wav（建议以 `hash(clean_markdown(text) + voice)` 命名，落在现有 reports 缓存目录），之后复用；配置文本变化（哈希不同）才重新生成。
4. **首次按键即触发、随后停止监听**：通话中收到**第一个** DTMF 按键即触发**该键**的 `hook`（`{call_id}` / `{digit}`），随后停止监听按键（忽略后续按键）。`last_digit` 记为该首次按键；挂机 hook `{last_digit}` 即此值（未按键为空串）。原生 DTMF 实现：在 `pjsua2_backend._make_classes` 的 `Call` 子类加 `onDtmfDigit(digit)` 并 `backend._handler("dtmf", {"call_id", "digit"})`；`FakeSipBackend` 加 `receive_dtmf(call_id, digit)` 测试钩子；`SipCoreService` 维护每通呼入的 `last_digit` 并新增 `EVENT_DTMF` 事件/常量与 `dtmf` 分发。

## Red line

播放（单向把合成文件送进通话）已是 `teleflow-phone-report` 允许的红线例外；DTMF 仅读取带内 / RFC 2833 按键信令，不属于"录制通话 / 对通话做 DSP"。仍禁止对通话录音、写通话 WAV、对通话做 DSP。

## Out of Scope

- 多分机并发 IVR、按键后多级菜单/转接/会议。
- 对通话的录音或 DSP。
