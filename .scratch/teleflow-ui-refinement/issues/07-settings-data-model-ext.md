# 07 — 扩展 Settings 数据模型：网关配置 + 账号管理

**Feature:** teleflow-ui-refinement

**Status:** ready-for-agent

**Blocked by:** 03 — Settings modal

---

## 目标

当前 `Settings` 数据模型（`src/teleflow/config.py`）仅包含：`sip_port`、`playback_device_id`、`capture_device_id`、`autostart`、`start_minimized`、`log_level`。原型图设置模态框新增了网关配置和账号管理字段，需要扩展数据模型和持久化。

## 新增字段

```python
@dataclass
class Settings:
    # 现有字段
    sip_port: int = 5060
    playback_device_id: str = ""
    capture_device_id: str = ""
    autostart: bool = False
    start_minimized: bool = False
    log_level: str = "INFO"

    # 新增字段（原型图要求）
    gateway_port: int = 5060           # 网关端口
    gateway_password: str = ""          # 网关注册密码
    sip_number: str = "1001"            # SIP 号码
    accounts: list[str] = field(default_factory=list)  # 分机号列表
```

## 原型要求

原型图设置模态框（第 247-266 行，第 115-116 行）包含：

### 网关配置
- **网关端口** — 数字输入，默认 5060
- **网关密码** — 密码输入框（`input type="password"`）
- **SIP 号码** — 文本输入，默认 "1001"

### 账号管理
- 文本输入框 + 「添加账号」按钮
- 已添加的账号以标签（Pill）形式展示：`<span class="pill">1001</span>`
- 当前没有账号时显示「尚未添加账号」

## 实现要点

- 扩展 `Settings` dataclass，确保序列化/反序列化兼容（`asdict` 和 `__init__` 的默认值）
- `ConfigStore` 需要支持 `accounts` 列表的 JSON 序列化
- 账号管理 UI 组件：
  - `QLineEdit` 输入 + `QPushButton` 添加
  - `QHBoxLayout` 或 `QFlowLayout` 展示标签
  - 每个标签（Pill）是一个 `QLabel` + 关闭按钮（可选，原型图未显示删除功能，但建议实现）
- 网关密码输入框使用 `QLineEdit.setEchoMode(QLineEdit.EchoMode.Password)`
- 保存时将 `accounts` 列表持久化到 JSON 配置文件

## 验收标准

- [ ] `Settings` 数据模型包含 `gateway_port`、`gateway_password`、`sip_number`、`accounts` 字段
- [ ] 设置模态框显示网关端口、网关密码、SIP 号码输入框
- [ ] 网关密码输入框显示为密码掩码
- [ ] 可以添加账号，账号以标签形式展示在 UI 中
- [ ] 保存设置后重新打开，所有字段值持久化保留
- [ ] 配置文件向后兼容（旧版本配置文件缺少新字段时使用默认值）
- [ ] 账号列表为空时显示「尚未添加账号」提示

## 参考

- 原型图：`prototypes/teleflow-ui-prototype.html`（第 247-266 行，第 115-116 行）
- 现有代码：`src/teleflow/config.py` — `Settings`、`ConfigStore`