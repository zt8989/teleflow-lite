# 05 — 系统托盘菜单增强（启停 + 显示 + 设置 + 退出）

**Feature:** teleflow-ui-refinement

**Status:** ready-for-agent

**Blocked by:** (none — applies to existing `app.py` tray)

---

## 目标

当前系统托盘菜单仅有「启动/停止 SIP 服务」「显示窗口」「退出程序」三项。原型图要求更丰富的菜单结构，并且设置入口仅存在于托盘菜单中（Dashboard 上没有设置入口）。

## 原型要求

原型图（第 135-142 行）的托盘菜单包含：

```
▶ 启动服务         (Start SIP service)
⏸ 停止服务         (Stop SIP service)
🪟 显示窗口         (Show window)
⚙ 设置            (Open settings modal)
───────────────     (分隔线)
⏻ 退出             (Quit)
```

- 启动/停止二选一显示（服务运行中显示「停止服务」，停止时显示「启动服务」）
- 点击「设置」打开设置模态框（见 ticket 03）
- 点击「退出」退出程序（`force_quit = True` 后 `QApplication.quit()`）
- 使用 Unicode 图标或 `QIcon` 装饰菜单项

## 实现要点

- 当前 `_setup_tray()` 已创建菜单，需要调整菜单项顺序和内容
- 设置菜单项需要引用 `SettingsDialog` 实例（或回调打开设置模态框）
- 菜单项图标使用 Unicode 符号（如 `▶`、`⏸`、`🪟`、`⚙`、`⏻`）或 `QIcon.fromTheme()`
- 保持 `_sync_sip_button()` 的启停标签同步逻辑
- 托盘图标可以替换为原型图提供的 SVG 图标（`prototypes/teleflow-icon.svg`）

## 验收标准

- [ ] 托盘右键菜单包含：启动/停止服务、显示窗口、设置、分隔线、退出
- [ ] 启动/停止菜单项根据服务运行状态自动切换文本
- [ ] 点击「设置」打开设置模态框
- [ ] 点击「退出」完全退出应用程序
- [ ] 托盘图标使用原型图提供的 SVG 图标
- [ ] 托盘图标的颜色反映服务状态（运行中=绿色，停止=灰色）

## 参考

- 原型图：`prototypes/teleflow-ui-prototype.html`（第 93-106 行，第 135-142 行）
- 原型图标：`prototypes/teleflow-icon.svg`
- 现有代码：`src/teleflow/app.py` — `MainWindow._setup_tray()`、`_sync_sip_button()`