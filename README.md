# TeleFlow — 座机声音流转助手

TeleFlow 是一个本地 **SIP 用户代理（UA）** 桌面程序（PyQt6）。它监听来自电话网关（ATA）呼入当前 SIP 账号的来电，**自动应答** 并把通话音频无损桥接到用户自选的声卡：下行写入播放设备、上行从采集设备取流。播放与采集设备可独立选择（例如生产模式用虚拟声卡，调试模式用耳机）。

- 真实传输层：`pjsua2` 原生库（见 `docs/build-pjsua2.md`）。
- 测试/无界面环境：`FakeSipBackend` 脚本化网关，无需网络或原生库即可跑通全部逻辑。

---

## 快速开始

```bash
# 依赖（Python >= 3.10）
pip install -e .            # 安装 PyQt6 等运行时依赖
# pjsua2 原生库需单独构建，见 docs/build-pjsua2.md
python -m teleflow.app     # 启动 GUI
```

启动后窗口最小化到系统托盘：右下角托盘菜单可 **启动/停止 SIP 服务、显示窗口、设置、退出**。`docs/packaging.md` 说明 macOS DMG 打包。

---

## 目录结构

| 路径 | 职责 |
|------|------|
| `src/teleflow/sip.py` | SIP 核心服务 `SipCoreService` 与 `SipBackend` 协议（真实/假后端可互换） |
| `src/teleflow/pjsua2_backend.py` | 基于 pjsua2 的真实传输层 |
| `src/teleflow/hooks.py` | **通话生命周期外部命令 hook**（见下） |
| `src/teleflow/config.py` | 设置持久化（`Settings` + `ConfigStore`） |
| `src/teleflow/app.py` | PyQt6 应用外壳、仪表盘、托盘、设置弹窗 |
| `src/teleflow/audio.py` / `media.py` | 音频设备枚举与会议桥路由 |
| `prototypes/teleflow-ui-prototype.html` | UI 原型（含 hook 配置界面） |

---

## 音频路由：双向调试 vs 单向中继（生产模式）

TeleFlow 只做**纯音频路由器**：把已建立的 RTP 会话桥到所选设备——下行（电话 → 播放设备）、上行（采集设备 → 电话）。播放与采集设备**可独立选择**，派生出两种典型用法：

| 模式 | 播放设备 | 采集设备 | 行为 | 系统麦克风提示 |
|------|----------|----------|------|----------------|
| 调试模式（耳机） | 物理耳机 | 物理麦克风 | 双向：用座机正常通话 | 会（用了真麦克风） |
| 生产模式（虚拟声卡） | VB-Cable / BlackHole | **不采集（空）** | **单向**：仅把座机语音写出到虚拟声卡 | **不会**（没开任何采集端点） |

**生产模式 = MicroSIP 风格**：是否打开麦克风完全由"是否选了输入设备"决定。采集设备留空即单向，TeleFlow 不打开任何音频输入端点（内部置 `PJSUA_SND_NULL_DEV`），系统不弹麦克风隐私提示。呼入 IVR 不再另设单向模式：只要选了采集设备，IVR 播报期间通话同样双向桥接，AI 侧可随时插话（见下节）。

### 典型部署：座机语音经 VB-Cable 喂给三方 APP

把座机通话语音实时送到另一个程序（语音助手 / 转写 / 录音）当麦克风输入：

```
固定座机
   │ 模拟电话线
   ▼
ATA（模拟电话适配器，转 SIP）
   │ SIP
   ▼
FreeSWITCH（IP-PBX，路由 / 注册）
   │ SIP INVITE
   ▼
TeleFlow（本程序，自动应答）
   │ 下行音频（仅播放，不采集）
   ▼
VB-Cable（虚拟声卡 · 播放端）
   │ 系统把其"录音端"呈现为麦克风
   ▼
三方 APP（把 VB-Cable 录音端选作"麦克风"输入）
```

- TeleFlow 在此链路里只是 VB-Cable 的**写入方**：选「生产模式（虚拟声卡）」后，播放 = VB-Cable、采集 = 空。
- VB-Cable **播放端**被 TeleFlow 写入（输出，不触发麦克风提示）；其**录音端**由三方 APP 打开当麦克风——那一侧的提示属于三方 APP，理应保留。
- 红线不变：TeleFlow 仍不录音、不做 DSP，仅透传座机语音到虚拟声卡。

---

## 呼入 IVR：欢迎语 + 每数字键播报 / Hook

呼入自动应答后，TeleFlow 可进入一段简单 IVR：先播放**欢迎语**，再按 `1-9-0` 顺序逐项播放每个数字键**各自**的配置文字（菜单），然后监听来电者的首个 DTMF 按键，触发**该键**对应的 hook 命令。每个键独立配置 `text`（播报词）与 `hook`（命令），文字为空的键跳过不播、无 hook 的键按下时不执行命令。`ivr_enabled` 为总开关（默认开），关闭即回到纯音频路由器。

```
座机来电（INVITE）
   │
   ▼
TeleFlow 自动应答（CALL_CONNECTED）
   │  ivr_enabled = True
   ▼
播放 欢迎语（TTS → 8k mono wav，可缓存）；通话始终双向桥接，AI 侧可随时插话或打断
   │
   ▼
按 1-9-0 顺序播放各键 text（空文字键跳过不播）
   │
   ▼
开启 DTMF 监听，等待首个按键
   │
   ▼
来电者按 <键>
   ├── 该键 text 非空 → 菜单里已播过（无需重播）
   ├── 该键 hook 非空 → 执行该键命令（{call_id} / {digit}）   ← 每键独立
   └── 停止监听后续按键；last_digit = 该键
   │
   ▼
挂机（CALL_ENDED）
   │
   ▼
执行 on_hook_cmd（{last_digit} 被替换，未按键则为空串）
```

- **每键独立**：`ivr_digit_text` / `ivr_digit_hook` 均以 `"1".."9"`、`"0"` 为键；某键无文字则菜单中不播，某键无 hook 则按下时不执行命令。
- **语音缓存**：欢迎语与各键 `text` 首次 TTS 后按 `hash(clean_markdown(text)+voice)` 缓存 wav，同文本再次呼入直接复用。
- **通话始终双向**：IVR 播报期间不压话筒，AI 侧（经采集设备）可随时插话或打断来电者，就像 10010 那样的「语音播报 + 实时聆听」。菜单仅由 DTMF 按键驱动，通话挂机时结束；不存在「退出 IVR 切回双向」的单独开关——只要选了采集设备，呼入通话即双向桥接。
- 红线不变：IVR 播报仅做 TTS 播放与 DTMF 读取，不录音、不做 DSP；通话虽双向桥接，但 TeleFlow 不录音、不写通话 WAV、不做任何变换。

---

## 电话汇报（外呼 + 单向播放）：本地 RPC 控制通道

TeleFlow 不仅能接呼入，还能**主动外呼**物理座机并播放一段汇报。外部脚本（如 AI 助手的 Stop hook）用一条带 token 的本地 HTTP 请求（`POST /v1/report`）把**文本**交给 TeleFlow，由它内部完成 TTS 合成、转码、外呼、播放、播完挂断——外部脚本无需自己实现 TTS 或 SIP。这就是「通过 hook 反向给对应号码打电话」。

```
外部脚本 / hook（AI 助手任务完成）
   │ POST /v1/report  { "text": "…", "voice"?: "…" }
   │ Authorization: Bearer <rpc_token>
   ▼
TeleFlow 本地 RPC 服务（127.0.0.1:<rpc_port>，默认 8731）
   │ 校验 token / SIP 状态 / 座机目标 report_target
   ▼
TTS 合成（edge-tts）→ ffmpeg 转 8kHz 单声道 wav（可缓存）
   │
   ▼
TeleFlow makeCall → 座机（report_target，如 sip:8000@192.168.1.116）
   │ 座机摘机（EVENT_CALL_CONNECTED）
   ▼
单向播放 wav 进通话（不桥接麦克风）
   │ 播放结束 EOF
   ▼
自动挂断（EVENT_REPORT_COMPLETED）
```

- **本地、受控**：RPC 仅绑 `127.0.0.1`，需 `Authorization: Bearer <rpc_token>`；token 首次启动随机生成并持久化，可在设置查看/重置。并发汇报返回 `409`（单汇报槽）。
- **文本即一切**：RPC 只发文本（+ 可选 `voice` 覆盖），合成/转码/拨号全在 TeleFlow 内；也支持 `audio_path` 覆盖跳过 TTS 直接播放。
- **配置**：`report_target`（座机目标 SIP URI）、`report_caller_id`、`tts_voice`、`ffmpeg_path`（空 = `PATH` 自动查找）、`rpc_enabled` / `rpc_port` / `rpc_token`，以及面板上的「测试汇报」按钮。
- 红线不变：汇报是**单向播放合成文件**，不录音通话、不写通话 WAV、不做 DSP。
- 另有 `POST /v1/play`（向活动呼入播放提示）与 `POST /v1/ivr/replay`（重播 IVR 菜单），以及 `GET /v1/status` 探测就绪状态。

---

## Hook 命令（摘机 / 挂机）

TeleFlow 可以在通话生命周期的关键时刻执行 **你配置的本地命令/脚本**。这是把来电事件接入外部自动化（弹窗通知、开门、写数据库、触发录音等）的最简单方式。

### 两个触发点

| 名称 | 配置字段 | 触发时机 | 事件 |
|------|----------|----------|------|
| **摘机（off-hook）** | `off_hook_cmd` | 当前 SIP **自动应答** 来电的瞬间 | `CALL_CONNECTED` |
| **挂机（on-hook）** | `on_hook_cmd` | 通话**结束**时（座机发送 `BYE`，或应答前 `CANCEL`） | `CALL_ENDED` |

### 配置

在托盘菜单 → **设置** 中填写「摘机命令」「挂机命令」（留空 = 不执行）。设置写入
`~/.config/teleflow/config.json`，**下次通话即生效，无需重启**。

命令中可用占位符 `{call_id}`（本次来电 ID）、`{last_digit}`（挂机前最后一个 IVR 按键，未按键则为空串）与 `{digit}`（IVR 按键事件的按键），会在执行时替换：

```
摘机命令：/usr/local/bin/on-answer.sh {call_id}
挂机命令：/usr/local/bin/on-hangup.sh {call_id} --last-digit {last_digit}
```

### 行为约定

- **非阻塞**：命令在后台线程（`daemon` 线程）中执行，`run()` 立即返回，绝不拖慢 SIP 信令或界面线程。
- **输出被丢弃**，退出码被忽略——hook 是旁路副作用，不是通话关键路径。
- **失败被吞掉并记入实时日志**：命令不存在或非零退出只会以 `[HOOK][ERROR] …` 形式出现在日志面板，不会中断通话。
- 示例日志：`[HOOK] 执行命令: /usr/local/bin/on-answer.sh CALL-AB12CD`。

### 平台示例

**macOS —— 用系统通知验证摘机：**

```
摘机命令：osascript -e 'display notification "摘机 {call_id}" with title "TeleFlow"'
```

**Linux —— 写一行到呼叫日志：**

```
摘机命令：echo "$(date) off-hook {call_id}" >> /var/log/teleflow-hooks.log
挂机命令：echo "$(date) on-hook  {call_id}" >> /var/log/teleflow-hooks.log
```

**任意脚本：** 直接写脚本路径即可，TeleFlow 以 `shell=True` 执行，因此可带参数与管道：

```
摘机命令：/opt/teleflow/on-answer.sh --id {call_id} | logger -t teleflow
```

### 安全须知

命令通过 `shell=True` 以**你本地配置中写定的模板**执行，并以**非交互方式**运行。两点请注意：

1. 只配置你自己信任的命令；配置文件的读写为本地用户权限。
2. `{call_id}` 的值来自网关发来的 INVITE（即对方提供的 call-id），会**原样替换进命令行**。在不可信网络/网关上，恶意的 call-id 可能构成 shell 注入。若环境不可信，请勿把 `{call_id}` 直接拼进 shell 命令，或仅在内网/可信网关下使用本功能。

---

## 开发与测试

```bash
pip install -e ".[dev]"     # pytest + mypy
pytest                       # 全量单测（含 FakeSipBackend 脚本化网关）
mypy src/teleflow            # 类型检查
```

- 测试通过 `pythonpath=["src"]` 解析包，并将 SIP/音频后端替换为假实现，无需显示器或原生库（CI 用 `QT_QPA_PLATFORM=offscreen`）。
- 功能开发建议在独立 git worktree 中进行，并把该功能的 `.scratch/<slug>` issue 一并带入 worktree（保持 `master` 工作树干净），见 `.scratch/` 下的 issue 跟踪约定（`docs/agents/issue-tracker.md`）。

## UI 原型

`prototypes/teleflow-ui-prototype.html` 是一个可交互的 HTML 原型：用托盘菜单打开「设置」即可看到摘机/挂机命令输入框；用「演示事件」按钮触发呼入/挂断，日志面板会以紫色 `[HOOK]` 行展示命令执行情况。
