# 04: 主窗口 / 托盘 / 实时动态切换（含 TTS 音色标签）

**What to build:** 把主窗口与系统托盘菜单、弹窗提示、状态文本接入 `tr()`，并把「设置里改语言 → 整个应用即时切换、无需重启」打通。交付：

- `MainWindow`（`app.py`）托盘菜单动作（启动/停止 SIP、显示窗口、设置、测试汇报、退出）、状态标签、以及 `QMessageBox` 文本走 `tr()`。
- `config.py` 的 TTS 音色下拉显示名（`晓晓（女）` 等）改为可翻译：翻译**显示标签**，音色 ID 映射保持不变；中英文成对写入 locale。
- 在 Settings 保存时：持久化 `Settings.language` 并调用 `i18n.set_language(lang)`。
- 订阅 `i18n` 的变更通知：语言切换时 `MainWindow` 主动对「自己 + 已打开的 `DashboardWidget` + 已打开的 `SettingsDialog`」调用各自 `retranslate()`，实现实时动态切换。
- 进程启动：`MainWindow` 初始化时按 `Settings.language`（含 `"auto"` 解析）设定 `i18n` 当前语言，保证首屏语言正确。

**Blocked by:** 02 (仪表盘 retranslate)、03 (设置对话框 retranslate + 语言选择器)。

**Status:** ready-for-agent

- [ ] 托盘菜单、状态标签、`QMessageBox` 文本均通过 `tr()` 取值。
- [ ] 保存设置后整个应用（托盘、仪表盘、状态、已打开对话框）即时切换语言，无需重启。
- [ ] TTS 音色下拉显示名随语言切换（ID 不变）。
- [ ] 启动首屏按 `Settings.language`（含 `"auto"`）正确定语言。
- [ ] 内部日志行（`[HOOK]`/`[IVR]`/`[REPORT]`/`[SIP]`/`[TTS]` 前缀及其正文）**保持英文**，不被翻译。
