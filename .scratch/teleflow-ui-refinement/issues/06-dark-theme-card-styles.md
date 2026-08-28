# 06 — 深色主题 + 卡片样式 + 布局响应式

**Feature:** teleflow-ui-refinement

**Status:** ready-for-agent

**Blocked by:** 01 — Dashboard card-based layout

---

## 目标

原型图使用深色主题（`#0f1115` 背景 + `#171a21` 卡片底色），所有 UI 元素具有圆角、阴影、统一的颜色变量体系。当前 PyQt6 应用使用系统默认样式，需要应用全局样式表（QSS）以匹配原型图设计。

## 原型配色方案

| 令牌 | 值 | 用途 |
|------|-----|------|
| `--bg` | `#0f1115` | 窗口背景 |
| `--panel` | `#171a21` | 卡片背景 |
| `--panel-2` | `#1d212b` | 输入框/二级面板背景 |
| `--line` | `#2a2f3a` | 边框/分隔线 |
| `--text` | `#e7eaf0` | 主文字色 |
| `--muted` | `#9aa3b2` | 辅助文字/标签 |
| `--accent` | `#4ea1ff` | 主色调（按钮、选中态） |
| `--accent-2` | `#2b6fb8` | 主色调悬浮态 |
| `--ok` | `#3ecf8e` | 运行中/成功状态 |
| `--warn` | `#f5a623` | 警告/呼入状态 |
| `--bad` | `#ff5d5d` | 错误/挂断状态 |
| `--idle` | `#8b93a3` | 空闲状态 |

## 卡片样式

- 圆角 12px（`border-radius: 12px`）
- 边框 1px solid `--line`
- 背景 `--panel`
- 阴影 `0 8px 30px rgba(0,0,0,.35)`
- 卡片标题：小号（13px）大写字母，`--muted` 颜色，`letter-spacing: .6px`

## 状态标签（Pill）样式

- 圆角标签，带颜色指示圆点（8px 直径）
- 服务运行中：蓝色圆点 + 发光阴影
- 空闲监听中：蓝色圆点
- 通话中：绿色圆点
- 呼入：黄色圆点
- 挂断/已停止：红色圆点
- 空闲：灰色圆点

## 实现要点

- 在 `MainWindow` 或 `QApplication` 上设置全局 QSS 样式表
- 使用 `setStyleSheet()` 方法，或在 `__init__` 中加载 `.qss` 文件
- 卡片容器的实现：使用 `QFrame` 设置 `setProperty("class", "card")`，配合 QSS 选择器
- 状态指示圆点：使用 `QLabel` 设置固定大小 + 圆角 border-radius 实现
- 输入框、下拉框、按钮等统一样式覆盖
- 所有组件使用 `QFont` 统一字体栈（`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif`）

## 验收标准

- [ ] 窗口背景为深色（`#0f1115`）
- [ ] 卡片背景为 `#171a21`，圆角 12px，带阴影
- [ ] 输入框/下拉框背景为 `#1d212b`，聚焦时边框变为主色调
- [ ] 状态标签（Pill）显示正确的颜色指示圆点
- [ ] 主按钮使用主色调（`#4ea1ff`），悬浮时变深
- [ ] 文字颜色统一为 `#e7eaf0`，辅助文字为 `#9aa3b2`
- [ ] 窗口宽度低于 720px 时，状态统计网格从 4 列变为 2 列
- [ ] 使用 PingFang SC / Microsoft YaHei 等中文字体优先

## 参考

- 原型图：`prototypes/teleflow-ui-prototype.html`（第 25-119 行 CSS）
- 现有代码：`src/teleflow/app.py` — `MainWindow`