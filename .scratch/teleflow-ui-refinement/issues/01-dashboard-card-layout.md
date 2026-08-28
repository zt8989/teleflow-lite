# 01 — Dashboard card-based layout (替代当前 Tab 布局)

**Feature:** teleflow-ui-refinement

**Status:** ready-for-agent

**Blocked by:** (none — applies to existing `app.py` UI surface)

---

## 目标

当前 `MainWindow` 使用 `QTabWidget` 将「状态」「设置」「日志」分到三个 Tab 页。原型图要求一个**单页 Dashboard**，以卡片（Card）形式集中展示所有关键信息，设置移入系统托盘菜单中的模态框。

## 原型要求

原型图（`prototypes/teleflow-ui-prototype.html`）展示了一个单页 Dashboard，包含：

### 1. 状态统计网格（4 张卡片，2×2 或 4 列）
- **SIP 服务** — 运行中 / 已停止 / 空闲监听中，带颜色指示点
- **网关注册** — 已注册 IP:端口 / 未注册
- **当前模式** — 调试模式 / 生产模式
- **通话状态** — 空闲 / 呼入 / 通话中 / 挂断

### 2. 音频设备路由卡片
- 播放设备（下行）下拉选择器
- 采集设备（上行）下拉选择器
- 刷新设备按钮
- 模式预设按钮：调试模式（耳机）/ 生产模式（虚拟声卡）

### 3. 实时日志卡片
- 固定高度（约 220px）可滚动日志区域
- 日志按类型着色：SIP（蓝色）、Media（绿色）、Device（黄色）、Error（红色）
- 每行带时间戳

## 变更范围

- `src/teleflow/app.py` — 重构 `MainWindow` 的 UI 布局
  - 移除 `QTabWidget`，改用垂直布局 + 卡片式 `QFrame`/`QGroupBox`
  - 将 `StatusPanel` 替换为 4 个卡片状态网格（`QGridLayout` 或 4 列 `QHBoxLayout`）
  - 将音频设备选择器从 `SettingsPage` 移到主 Dashboard 的音频路由卡片
  - 将日志视图从独立 Tab 移到 Dashboard 底部的日志卡片
  - 保留 `SettingsPage` 但仅通过系统托盘菜单的「设置」打开（见 ticket 03）
  - 移除 `closeEvent` 中的 `settings_page.save()` 逻辑，改为 Settings 模态框关闭时保存

## 验收标准

- [ ] 应用启动后显示单页 Dashboard，没有 Tab 栏
- [ ] 顶部显示 4 个状态卡片：SIP 服务、网关注册、当前模式、通话状态
- [ ] 中间显示音频设备路由卡片，含两个独立下拉框 + 刷新按钮 + 模式预设按钮
- [ ] 底部显示日志卡片，2000 行上限，自动滚动到底部
- [ ] 所有状态卡片随 SIP 事件实时更新（通过 `SipCoreService` 的事件总线）
- [ ] 音频设备选择变更通过 `manager.set_selection()` 生效，触发 `reroute()`
- [ ] 最小化到托盘时仍能保持状态更新

## 参考

- 原型图：`prototypes/teleflow-ui-prototype.html`
- 现有代码：`src/teleflow/app.py` — `MainWindow`、`StatusPanel`、`SettingsPage`