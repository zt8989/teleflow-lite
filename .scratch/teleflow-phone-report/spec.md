# TeleFlow — 电话汇报 RPC（Phone Report RPC）

- **Status:** `ready-for-agent`
- **Labels:** `ready-for-agent`
- **Source:** 用户需求：hook 之后由外部脚本通知本软件，触发本机拨打物理座机，接通后播放声音。参考用户既有 FreeSWITCH/WorkBuddy 实现（`notify_phone.py` / `call_and_play.py`）。
- **Feature slug:** `teleflow-phone-report`
- **前置 spec：** `teleflow-v1.0`（本软件是 macOS + pjsua2 本地 SIP UA，原设计是"纯音频路由器"，本 spec 在其上叠加"汇报播放"能力）

---

## Problem Statement

用户运行一个外部 AI 助手（如 WorkBuddy）。当该助手完成任务时，会触发一个 **Stop hook**，脚本希望通知 **本机正在运行的 TeleFlow** 去 **拨打物理座机**，并在 **座机接通后播放一段汇报音频**，播完自动挂断。

在用户既有的 Windows 方案里，这一步由 `notify_phone.py` 完成：检测到 `__PHONE_REPORT__` 标记 → edge-tts 合成 → ffmpeg 转 8kHz wav → 经 FreeSWITCH ESL `originate sip:8000@192.168.1.116 &playback(wav)&hangup()` 呼叫语音网关的 FXS 座机并播放。

TeleFlow 已是 macOS 上的本地 pjsua2 SIP UA，能接受网关注册、自动应答呼入、并把通话音频桥接到用户选择的声卡。但它**目前没有"向外拨号 + 单向播放文件 + 播完挂断"**的能力，也**没有让外部进程与之通信的通道**，且**没有内置 TTS**。本 spec 要补上这三块：

1. 一个 **本地 HTTP RPC 控制通道**，让外部 hook 脚本用一条 HTTP 请求（带入文本）触发汇报。
2. **内置 TTS 合成层**：依赖 `edge-tts` 把汇报文本合成为语音，音色可配置；ffmpeg 作为**外部二进制**把 mp3 转码为 pjsua2 可播放的 8kHz 单声道 wav（ffmpeg 支持 PATH 自动查找或手动指定路径）。
3. 在 SIP 后端/服务层新增 **汇报播放流**：向外拨打座机 → 接通后将合成的 WAV 文件单向播放进通话 → 播完自动挂断。

### 关键澄清（已与用户确认，2026-08-28 修订）

- **RPC 传输 = 本地 HTTP 服务**（`127.0.0.1`，带共享 token 鉴权），语言无关，外部脚本可用 curl/requests 调用。
- **音频来源 = TeleFlow 内置 TTS（edge-tts）**：外部脚本只发送**文本**（+ 可选 `voice` 覆盖），TeleFlow 负责合成与转码。用户可在设置里配置默认音色（如 `zh-CN-XiaoxiaoNeural`）。（首版曾考虑"外部提供 wav 路径"，已改为内置 TTS。）
- **ffmpeg = 外部二进制**：不随包内置，运行时通过 `PATH` 自动查找（`shutil.which("ffmpeg")`），或用户在设置里手动指定绝对路径；都找不到时汇报返回明确错误。
- RPC 仍支持可选的 `audio_path` 覆盖：若外部脚本已自带 wav，可跳过 TTS 直接播放（高级用法）。

---

## Solution

TeleFlow 增加一个常驻的 **本地 RPC 服务**（`127.0.0.1:<rpc_port>`，默认 `8731`）和一个 **汇报控制器**，并新增 **TTS 合成层**。外部 hook 脚本完成文本提取后，向 `POST /v1/report` 发送 `{ "text": "..." }`；TeleFlow 先清理 Markdown 符号 → 用 `edge-tts` 合成 mp3 → 用外部 `ffmpeg` 转 8kHz 单声道 wav → 校验后通过 pjsua2 向座机（配置的目标 SIP URI，如 `sip:8000@192.168.1.116`）发起外呼，座机摘机（`EVENT_CALL_CONNECTED`）后把 wav **单向**播放进通话，播放结束时（`AudioMediaPlayer` EOF）自动挂断，并全程写 `[REPORT]` 日志。

这与 V1.0 的"纯音频路由器"红线不冲突的核心前提：**红线禁止的是"录制通话 / 把通话写 WAV / 对通话做 DSP"**。本能力新增两条明确例外——

1. **单向播放外部/合成文件到通话**（playback，非 recording）；
2. **为播放而临时合成一个 wav 文件**（TTS 产物，是对"待播报文本"的渲染，不是对任何通话的录音；属瞬态产物，可放缓存目录并在播放后清理或保留供调试）。

这两点都不涉及对真实通话的采集或处理，红线的"不录音通话"语义保持不变。

### 与现有能力的边界

- **呼入（已有）**：网关 INVITE → 自动应答 → 双向桥接到用户声卡（调试/生产模式）。保持不变。
- **汇报（新增，外呼）**：TeleFlow 主动 `makeCall` 到座机 → TTS 合成 wav → 接通后**单向**把 wav 播放进通话 → EOF 挂断。不桥接麦克风。
- 两者可并存（外呼汇报进行中收到呼入，呼入仍按原逻辑应答桥接）。首版约束：**同一时刻只允许一个汇报任务**；并发的 `POST /v1/report` 返回 `409`。

---

## User Stories

1. 作为外部 hook 脚本，我想用一条带 token 的 HTTP 请求把**汇报文本**发给 TeleFlow，让它自己合成语音并拨打座机播放，这样我无需自己实现 TTS 与 SIP。
2. 作为用户，我希望 RPC 仅在 `127.0.0.1` 监听且需 token，这样本机其他进程不能随意触发拨号。
3. 作为用户，我希望能在设置里开关 RPC、改端口、查看/重置 token，这样我能按需控制暴露面。
4. 作为用户，我希望在设置里填写座机的目标 SIP URI（如 `sip:8000@192.168.1.116`）、主叫名，以及**默认 TTS 音色**，这样汇报能打到正确的物理座机且声音符合预期。
5. 作为用户，我希望 ffmpeg 能自动找到（PATH 里有即可），也允许在设置里手动指定其绝对路径，这样没有全局 ffmpeg 时也能用。
6. 作为用户，我希望座机接通后才开始播放、播完自动挂断，这样不会出现"未接通就放音"或"一直占线"。
7. 作为用户，我希望若 SIP 未启动 / 未注册网关 / 未配置座机目标 / wav 文件缺失 / ffmpeg 找不到 / TTS 失败，RPC 立即返回明确错误（而非静默失败），这样 hook 脚本能可靠判断成败。
8. 作为用户，我希望汇报全流程出现在实时日志里（开始/接通/播放中/完成/失败，含 TTS 与转码步骤），这样我能像排查 FreeSWITCH 那样排障。
9. 作为用户，我希望 tray 菜单和面板上有"测试汇报"入口（呼叫座机播放一段测试文本/指定文本），这样我无需每次都走完整 hook 即可验证链路。
10. 作为开发者，我希望 TTS 与播放逻辑能用既有的"脚本化 SIP peer"（FakeSipBackend）+ 假 TTS 后端在 CI 中无硬件、无 pjsua2、无网络地测试。
11. 作为用户，我希望红线的"不录制通话"语义在汇报功能下依然被测试守住（仅允许播放与合成瞬态文件）。

---

## Implementation Decisions

### Modules to build（逻辑模块，不绑定具体文件名）

- **RPC 服务（控制通道）** — 在 `build_app` 中随应用启动的后台线程 HTTP 服务，绑定 `127.0.0.1:<rpc_port>`。两个端点：
  - `POST /v1/report`：JSON body `{ "text": str(必填，除非给了 audio_path), "audio_path"?: str, "voice"?: str, "target"?: str, "caller_id"?: str }`。
    - 若提供 `audio_path`：跳过 TTS，直接用该 wav。
    - 否则用 `text`：经 `clean_markdown` 清洗 → `edge-tts` 合成 mp3（音色取 `voice` 或设置默认 `tts_voice`）→ 外部 `ffmpeg` 转 8kHz 单声道 wav → 播放。
    - 校验 token → 校验 SIP 状态/座机目标/文件 → 触发汇报 → 返回 `202 Accepted` + `report_id`；失败返回 `400`/`401`/`409` 并带可读 `error`（如 `ffmpeg not found`、`tts failed`）。
  - `GET /v1/status`：返回 JSON（rpc 启用、sip 运行、网关注册、当前通话状态、是否有汇报进行中、当前 tts_voice、ffmpeg 路径）。供 hook/脚本探测就绪。
  - 鉴权：从 `Authorization: Bearer <token>` 读取；缺失/错误返回 `401`。token 缺失时首次启动自动生成随机值并持久化到配置，用户可从设置查看。
  - 并发：单汇报槽；进行中再收到 `POST /v1/report` 返回 `409`。
  - **线程注意**：HTTP handler 不得在 pjsua2 worker 线程里直接调 SIP；应把"触发汇报"调度回 Qt 主线程（如 `QMetaObject.invokeMethod` / 信号槽），由主线程调用 `SipCoreService`。TTS（edge-tts，async/网络）与 ffmpeg（子进程）也应在触发前于合适线程完成，避免在 pjsua2 回调里做阻塞 I/O。

- **TTS / 音频合成层（新增）** — 负责把文本变成可播放 wav，隔离外部依赖以便测试：
  - `TtsBackend` 协议：`synthesize(text, voice) -> Path`（产出 mp3）与 `transcode(mp3_path, wav_path) -> Path`（ffmpeg 转 8kHz 单声道 `pcm_s16le`）。
  - 真实实现 `EdgeTtsBackend`：`edge-tts` 合成 mp3；**ffmpeg 发现** = 先用 `settings.ffmpeg_path`，若为空则 `shutil.which("ffmpeg")`，都失败抛 `FfmpegNotFound`；转码参数对齐用户 FreeSWITCH 方案：`-ar 8000 -ac 1 -c:a pcm_s16le`。
  - `clean_markdown(text)`：移植用户 `notify_phone.py` 的清洗逻辑（去 `*`/`#`/`|`/代码块/链接等），使 TTS 只念正文。放本层或独立工具函数。
  - `FakeTtsBackend`：返回预置的临时 wav 路径（不触网、不调 ffmpeg），供 CI 驱动控制器。
  - 产物目录：合成 wav 落在配置下的缓存/汇报目录（如 `~/.config/teleflow/reports/`），路径以**正斜杠/绝对路径**传入 pjsua2（用户文档强调反斜杠会被破坏）；播放后可清理或保留供调试（首版保留 + 简单上限）。

- **汇报控制器（扩展 SipCoreService）** — 编排汇报状态机：
  1. 校验（SIP running、已有可呼出的网关/目标 URI、文本或 audio_path 就绪、ffmpeg 可用）。
  2. 若需 TTS：调 `TtsBackend.synthesize` + `transcode` 得到 wav（失败 → `EVENT_REPORT_FAILED(reason="tts"/"ffmpeg")`）。
  3. `service.place_report_call(target, wav_path)` 发起外呼，记录 `report_call_id` 与状态 `REPORT_DIALING`。
  4. 收到该 call 的 `EVENT_CALL_CONNECTED` → 调后端 `play_file_to_call(call_id, wav_path)`，状态 `REPORT_PLAYING`，发 `EVENT_REPORT_PLAYING`。
  5. 收到后端"播放结束"回调 → `hangup(report_call_id)`，状态 `REPORT_COMPLETED`，发 `EVENT_REPORT_COMPLETED`，复位空闲（可选清理临时 wav）。
  6. 任意一步失败 → 发 `EVENT_REPORT_FAILED(reason)`，复位空闲。
  - 新增领域事件常量（与现有 `EVENT_CALL_*` 并列）：`EVENT_REPORT_STARTED` / `EVENT_REPORT_CONNECTED` / `EVENT_REPORT_PLAYING` / `EVENT_REPORT_COMPLETED` / `EVENT_REPORT_FAILED`。建议新增独立 `ReportState` 枚举，避免与既有通话状态耦合。

- **SIP 后端汇报扩展（Pjsua2Backend + FakeSipBackend）** — 在 `SipBackend` 协议上新增：
  - `place_report_call(target: str, wav_path: str) -> None`：创建带"汇报"标记的 Call 实例后 `makeCall`（标记通过 `call._is_report=True` / `call._report_file=wav_path` 附着在 `Call` 子类实例上）。
  - `play_file_to_call(call_id: str, wav_path: str) -> None`：媒体激活后，构造 pjsua2 `AudioMediaPlayer`，`player.startTransmit(call_audio)`（**单向，文件→通话，不接麦克风**），并注册 EOF 回调，EOF 时回调服务触发挂断。
  - 现有 `onCallMediaState` 须按 call 是否为"汇报 call"分叉：汇报 call → 播放文件（**不再**桥接麦克风/扬声器）；普通呼入 call → 维持现有双向桥接不变。
  - `FakeSipBackend` 对应新增：`place_report_call` 记录 `(target, wav_path)`；新增脚本化钩子 `receive_report_connected(call_id)`（模拟座机摘机，触发服务播放）、`receive_report_playback_done(call_id)`（模拟 EOF，触发服务挂断）。这样控制器逻辑可在无 pjsua2、无硬件下端到端测试。

- **配置（ConfigStore / Settings）** — 在现有 `Settings` 上新增字段（默认值）：
  - `rpc_enabled: bool = True`
  - `rpc_port: int = 8731`
  - `rpc_token: str = ""`（空 → 首次启动随机生成并持久化）
  - `report_target: str = ""`（座机目标 SIP URI，如 `sip:8000@192.168.1.116`；空则 `POST /v1/report` 报错）
  - `report_caller_id: str = "TeleFlow"`
  - `report_hangup_on_eof: bool = True`
  - `tts_voice: str = "zh-CN-XiaoxiaoNeural"`（默认音色，可在 RPC `voice` 覆盖）
  - `ffmpeg_path: str = ""`（空 → `PATH` 自动查找；否则用指定绝对路径）
  - 说明：`report_target` 是新增字段，不复用既有 `gateway_port`/`sip_number`/`accounts`；RPC body 中的 `target` / `voice` 可临时覆盖配置。

- **UI / 设置（app.py）** — 设置弹窗新增两组：
  - RPC 设置：启用开关、端口、token 显示 + 重置按钮。
  - 汇报设置：座机目标 URI、主叫名、**默认 TTS 音色**（文本框，可填 `zh-CN-XiaoxiaoNeural` 等）、**ffmpeg 路径**（空=自动，或手动绝对路径）+ "测试 ffmpeg" 提示。
  - 面板：新增"电话汇报"状态卡（空闲 / 拨号中 / 播放中 / 完成 / 失败）与"测试汇报"按钮（弹输入或直接播默认测试文本）。
  - 托盘菜单：新增"测试汇报"动作。
  - 日志：新增 `[REPORT]` 分类（颜色建议紫色），并包含 `[TTS]` / `[FFMPEG]` 子步骤行，区别于既有 `[SIP]/[CALL]/[MEDIA]/[AUDIO]/[ERROR]`。

- **示例外部 hook 脚本（交付物，供用户 hook 工具参考）** — 在仓库提供 `examples/report_hook.py`：只负责从 hook payload 提取最后一条 assistant 文本 + 检测 `__PHONE_REPORT__` 标记 + `clean_markdown`，然后 `POST /v1/report`（带 token，body 仅含 `text`）。TTS 与转码全部交给 TeleFlow，脚本本身极薄。

### Interfaces / contracts

- **RPC 契约**
  - `POST /v1/report` 请求：`{ "text"?: str, "audio_path"?: str(与 text 二选一), "voice"?: str, "target"?: str, "caller_id"?: str }`；响应：`202 { "report_id": str }` 或 `4xx { "error": str }`。
  - `GET /v1/status` 响应：`{ "rpc_enabled": bool, "sip_running": bool, "gateway_registered": bool, "call_state": str, "report_in_progress": bool, "tts_voice": str, "ffmpeg_path": str }`。
  - 鉴权：`Authorization: Bearer <rpc_token>`，否则 `401`。
- **TtsBackend 契约**：`synthesize(text, voice) -> Path`、`transcode(mp3, wav) -> Path`，失败时抛明确异常（`TtsError` / `FfmpegNotFound` / `FfmpegError`）。
- **SipCoreService 新增契约**：`place_report_call(target, wav_path)`；新事件 `EVENT_REPORT_*`；汇报状态可由 `report_state` 属性查询（供 RPC `/v1/status` 与 UI 使用）。
- **SipBackend 协议新增**：`place_report_call(target, wav_path)`、`play_file_to_call(call_id, wav_path)`，并需暴露"播放结束"回调给服务。

### Architectural decisions

- **RPC 用本地 HTTP 而非 Unix socket / 文件队列**：语言无关、与用户现有 curl/Python hook 一致、便于排障；仅绑 `127.0.0.1` + token，暴露面可控。
- **TTS 内置（edge-tts），音色可配置**：用户明确要求 TeleFlow 依赖 edge-tts 且可配音色；RPC 传文本即可，外部脚本最薄。默认 `zh-CN-XiaoxiaoNeural`，可用 `voice` 请求级覆盖。
- **ffmpeg 是外部二进制，自动查找 + 手动指定**：macOS 不保证有 ffmpeg（如未装 chocolatey/brew），故支持 `PATH` 自动发现（`shutil.which`）与配置项手动路径双通道；都缺失时明确报错而非静默。这对应 FreeSWITCH 方案中写死的 `ffmpeg.exe` 绝对路径，但改为更通用的发现机制。
- **汇报是"外呼 + 单向播放"，与"呼入双向桥接"正交**：通过 per-call 的"汇报标记"在 `onCallMediaState` 分叉，改动局部化，不破坏 V1.0 的纯路由器路径。
- **红线例外明确化**：仅允许（a）向通话单向播放文件、（b）为播放而合成瞬态 wav（TTS 产物，非通话录音）。仍禁止对**通话**录音、写通话 WAV、对通话做 DSP。测试必须显式断言：汇报路径不采集/不录制任何通话音频。
- **单汇报槽 + 并发 409**：首版实现简单、行为可预测；后续如需队列再扩展。
- **复用既有"脚本化 SIP peer"测试缝 + 新增假 TTS 后端**：汇报控制器逻辑（校验→TTS→外呼→接通播放→EOF 挂断）完全可在 `FakeSipBackend` + `FakeTtsBackend` 上无硬件、无网络测试；pjsua2 播放与 edge-tts/ffmpeg 仅覆盖原生/外部-only 分支。

### Config record shape（新增字段）

```text
rpc_enabled: bool          = True
rpc_port: int              = 8731
rpc_token: str             = ""          # 空 → 首次启动随机生成并持久化
report_target: str         = ""          # 座机目标 SIP URI，如 sip:8000@192.168.1.116
report_caller_id: str      = "TeleFlow"
report_hangup_on_eof: bool = True
tts_voice: str             = "zh-CN-XiaoxiaoNeural"
ffmpeg_path: str           = ""          # 空 → PATH 自动查找；否则用指定绝对路径
```
（既有 `sip_port` / `playback_device_id` / `capture_device_id` / `autostart` / `start_minimized` / `log_level` / `gateway_port` / `gateway_password` / `sip_number` / `accounts` 不变。）

### Cross-platform notes

- macOS：edge-tts 为纯 Python（`pip` 安装，首次合成需联网）；ffmpeg 为外部二进制，优先 `PATH`，否则用户在设置里指定（如 `/opt/homebrew/bin/ffmpeg` 或 `/usr/local/bin/ffmpeg`）。
- 目标座机地址是用户环境相关的（网关 IP / FXS 号），必须以配置项提供给用户填写，不在代码里硬编码。
- 合成 wav 路径统一用正斜杠/绝对路径传入 pjsua2（用户文档强调 FreeSWITCH 下反斜杠 `\r` 会破坏路径；此坑在 pjsua2 播放路径上同样要避免）。

---

## Testing Decisions

- **沿用 V1.0 的"测外部行为而非实现" + "脚本化 SIP peer"缝**，并为 TTS 增加假后端：
  - **SIP 后端**：`FakeSipBackend` 新增 `place_report_call` / `receive_report_connected` / `receive_report_playback_done`，使控制器逻辑可无硬件、无 pjsua2 端到端测试。
  - **TTS 后端**：`FakeTtsBackend` 返回预置 wav，使"文本→wav→播放"链路可在无 edge-tts、无 ffmpeg、无网络下测试；真实 `EdgeTtsBackend`（edge-tts + ffmpeg 子进程）标 `# pragma: no cover`（原生/外部-only）。
  - **RPC 服务**：在测试中用空闲端口起真实本地 HTTP 服务（或注入请求），断言：无 token → `401`；SIP 未运行/无 `report_target` → `400`；`text` 与 `audio_path` 都缺 → `400`；进行中再请求 → `409`；成功路径 → `202` + 触发 TTS + `place_report_call`。
  - **汇报控制器**：驱动 `place_report_call` → `receive_report_connected` → 断言服务请求 `play_file_to_call` → `receive_report_playback_done` → 断言服务 `hangup` 且状态复位。
  - **ffmpeg 缺失路径**：注入"ffmpeg 找不到"的 TtsBackend/发现结果，断言 RPC 返回明确 `ffmpeg not found` 错误、不崩溃。
- **红線断言**：在"外呼汇报"完整路径上断言**不采集/不录制任何通话音频**、不插入 recorder/DSP（播放外部/合成文件不等于录音）。合成的瞬态 wav 是允许的，但必须断言它是对"文本"的渲染产物、而非来自通话。
- **Modules to test：**
  - *RPC 服务*：鉴权、参数校验、并发 409、状态端点。
  - *TTS 合成层*：`clean_markdown` 清洗、ffmpeg 发现（PATH/手动/缺失）、转码参数正确（8k 单声道 pcm_s16le）。
  - *汇报控制器*：状态机（拨号→TTS→接通→播放→完成 / 各失败分支）。
  - *FakeSipBackend*：新增汇报钩子行为。
  - *ConfigStore*：新字段 round-trip。
  - *红线*：汇报路径不录音通话、不写通话 WAV。

---

## Out of Scope

- 会议、IVR、转接。
- **录制通话、把通话写 WAV、对通话做 DSP**（红线不变；仅允许"播放文件到通话"与"为播放合成瞬态 wav"两条明确例外）。
- 跨机 RPC（仅 `127.0.0.1` 本地）。
- 用户既有 `phone_ctrl_d.py` 的"接通/挂断模拟 Ctrl+D 激活窗口"逻辑——那是外部 WorkBuddy 工具链的关注点，不在本软件内。
- 呼入自动应答桥接的改动（保持不变）。
- 音色市场/试听 UI（首版仅文本框填 voice 名 + 文本试播）。

---

## Further Notes

- 与用户 FreeSWITCH 方案的映射：
  - FreeSWITCH `api originate ... &playback(wav)&hangup()` → TeleFlow `SipCoreService.place_report_call(target, wav)` + 后端 `play_file_to_call` + EOF 挂断。
  - FreeSWITCH 方案里 `notify_phone.py` 的 edge-tts + ffmpeg + `clean_markdown` → 现**整体内移到 TeleFlow 的 TTS 合成层**；外部 hook 脚本只需发 `text`。
  - FreeSWITCH 写死的 `ffmpeg.exe` 绝对路径 → TeleFlow 改为 `PATH` 自动发现 + 可配置 `ffmpeg_path` 双通道。
  - FreeSWITCH `hook_debug.log` 诊断思路 → TeleFlow `[REPORT]`/`[TTS]`/`[FFMPEG]` 实时日志 + `GET /v1/status` 探测。
  - 路径正斜杠要求（用户文档强调 FreeSWITCH 下反斜杠 `\r` 会破坏路径）→ TeleFlow 同样以正斜杠/绝对路径传入 pjsua2，并在校验阶段规范化。
- **建议下一步**：确认本 spec 后，用 `/to-tickets` 拆成 tracer-bullet 工单（建议拆分：RPC 服务 + 鉴权、TTS 合成层 + ffmpeg 发现、配置字段、SIP 后端汇报扩展、汇报控制器状态机、FakeSipBackend/FakeTtsBackend 钩子、UI/设置、示例 hook 脚本、测试）。
