# 02 — 音频设备路由 UI 增强（独立选择 + 模式预设 + 刷新）

**Feature:** teleflow-ui-refinement

**Status:** ready-for-agent

**Blocked by:** 01 — Dashboard card-based layout

---

## 目标

当前 `SettingsPage` 已实现音频设备选择、刷新和模式预设，但它在独立的「设置」Tab 中，且原型图对交互有更具体的要求。本 ticket 将音频设备路由 UI 搬到 Dashboard 主界面，并增强交互细节。

## 原型要求

原型图的音频设备路由卡片包含：

1. 两个独立下拉选择器并列展示：
   - **扬声器 / 播放（下行）** — 从 `manager.playback_devices()` 填充
   - **麦克风 / 采集（上行）** — 从 `manager.capture_devices()` 填充
   - 每个设备名称后显示 `·虚拟` 标签（当 `kind === "virtual"` 时）
2. 刷新设备按钮
3. 模式预设按钮组（两个按钮，当前选中模式高亮）：
   - **调试模式（耳机）** — 切换到扬声器/耳机 + 麦克风/耳机
   - **生产模式（虚拟声卡）** — 切换到虚拟声卡输入 + 虚拟声卡输出
4. 设备变更时，日志面板追加一条「播放设备 -> xxx」或「采集设备 -> xxx」

## 需要适配的现有代码

- `src/teleflow/audio.py` — `AudioDeviceManager` 已有 `apply_preset()`、`refresh()`、`set_selection()`、`playback_devices()`、`capture_devices()`
- `src/teleflow/app.py` — `SettingsPage._on_preset()`、`_on_refresh()`、`_on_device_change()` 的逻辑需要迁移到 Dashboard 的音频卡片

## 实现要点

- 设备下拉框用 `QComboBox`，`device.kind` 为 `"virtual"` 时在显示名后加 `·虚拟`
- 模式预设按钮使用 `QPushButton`，当前选中模式添加 `.active` 样式（通过 `setProperty("class", "active")` + 样式表）
- 设备变更时调用 `manager.set_selection()`，并通过 `manager.on(EVENT_DEVICE_SELECTED, ...)` 触发 `service.reroute()`
- 刷新设备后日志模块记录设备枚举结果

## 验收标准

- [ ] 播放设备和采集设备下拉框在 Dashboard 音频卡片中，而非设置页面
- [ ] 虚拟声卡设备名称后显示 `·虚拟` 标签
- [ ] 刷新设备按钮重新枚举并更新下拉框
- [ ] 调试模式预设选中耳机设备，生产模式预设选中虚拟声卡设备
- [ ] 当前选中的模式按钮高亮显示
- [ ] 设备变更时日志面板追加对应记录

## 参考

- 原型图：`prototypes/teleflow-ui-prototype.html`（第 229-241 行）
- 现有代码：`src/teleflow/audio.py` — `AudioDeviceManager`
- 现有代码：`src/teleflow/app.py` — `SettingsPage`