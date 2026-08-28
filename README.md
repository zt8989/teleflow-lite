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

命令中可用占位符 `{call_id}` 表示本次来电 ID，会在执行时替换：

```
摘机命令：/usr/local/bin/on-answer.sh {call_id}
挂机命令：/usr/local/bin/on-hangup.sh {call_id}
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
