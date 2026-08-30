# 03: 设置对话框国际化 + 语言选择器

**What to build:** 把设置弹窗里所有用户可见文本改为走 `tr()`，并新增「语言 / Language」下拉，让用户在此切换界面语言。交付：

- `SettingsDialog` 的标签、占位符（`setPlaceholderText`，如欢迎语提示）、工具提示（`setToolTip`，如桥接键说明）、窗口标题「设置」等全部改为 `tr("<key>")`。
- 实现 `SettingsDialog.retranslate(self)`：重新绑定上述文本。
- 新增「语言 / Language」下拉（`QComboBox`）：选项 `en`（English）与 `zh_CN`（中文）；初始值取 `Settings.language`；保存时写回 `Settings.language`。
- 相关 key 写入 `en.json` 与 `zh_CN.json`。

**Blocked by:** 01 (i18n 核心机制 + `tr`).

**Status:** ready-for-agent

- [ ] 设置对话框标签/占位符/工具提示/窗口标题均通过 `tr()` 取值。
- [ ] `retranslate()` 覆盖上述文本，语言切换后（若对话框开着）即时刷新。
- [ ] 出现「语言」下拉，含 en / zh_CN 两项，初始值与保存均对接 `Settings.language`。
- [ ] 新增 key 在 `en.json` 与 `zh_CN.json` 中成对存在。
