# 03 — 设置模态框（从系统托盘菜单打开）

**Feature:** teleflow-ui-refinement

**Status:** ready-for-agent

**Blocked by:** 01 — Dashboard card-based layout

---

## 目标

当前设置页面是主窗口中的一个 Tab。原型图要求设置页面改为**模态框（Modal Dialog）**，仅从系统托盘右键菜单的「设置」按钮打开，Dashboard 上不显示设置入口。

## 原型要求

原型图的设置模态框（`prototypes/teleflow-ui-prototype.html` 第 146-152 行 + 第 247-266 行）包含：

### 基本设置
- **SIP 监听端口** — 数字输入，默认 5060，范围 1-65535
- **网关端口** — 数字输入，默认 5060
- **网关密码** — 密码输入框
- **SIP 号码** — 文本输入，默认 1001

### 账号管理
- 添加账号输入框（分机号/名称）
- 添加账号按钮
- 已添加账号以标签（Pill）形式展示

### 其他设置
- **日志级别** — 下拉框：DEBUG / INFO / WARNING / ERROR
- **开机自启** — 开关
- **最小化启动** — 开关

### 底部操作
- **保存设置** 按钮 — 保存并关闭模态框
- **关闭** 按钮 — 不保存关闭

## 实现要点

- 使用 `QDialog` 实现模态框，设置 `setWindowModality(Qt.WindowModality.ApplicationModal)`
- 系统托盘菜单的「设置」项连接 `settings_dialog.exec()` 或 `.show()`
- 保存时调用 `settings_page.save()` 写入 `ConfigStore`
- 账号管理目前是 UI 原型演示数据，实际存储可以暂存于内存列表（后续可扩展持久化）
- 网关端口/密码/SIP 号码当前不在 `Settings` 数据模型中，需要扩展 `Settings`（`dataclass`）或在 `ConfigStore` 中新增字段
- 扩展 `Settings` 数据模型：增加 `gateway_port`、`gateway_password`、`sip_number`、`accounts`（`list[str]`）

## 验收标准

- [ ] 系统托盘右键菜单点击「设置」弹出模态框
- [ ] 模态框包含 SIP 监听端口、网关端口、网关密码、SIP 号码、账号管理、日志级别、开机自启、最小化启动
- [ ] 已添加账号以标签形式展示，支持添加新的分机号
- [ ] 保存设置时写入 ConfigStore，关闭时不保存
- [ ] 重新打开模态框时显示已保存的值
- [ ] 模态框覆盖在整个应用之上，不可操作主窗口直到关闭
- [ ] Dashboard 上不再有设置 Tab

## 参考

- 原型图：`prototypes/teleflow-ui-prototype.html`（第 146-152 行，第 247-266 行）
- 现有代码：`src/teleflow/app.py` — `SettingsPage`
- 现有代码：`src/teleflow/config.py` — `Settings`、`ConfigStore`