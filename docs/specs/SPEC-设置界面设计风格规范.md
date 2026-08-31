# SPEC-设置界面设计风格规范

> 适用范围：设置对话框（`SettingsDialog`，`src/teleflow/app.py`）全部表单页——
> SIP 账号 / 挂钩 / IVR / 电话汇报 / 日志与启动。
> 目的：用一套统一的设计 token 约束所有表单控件的尺寸与间距，消除「高度不齐、
> 间距忽大忽小、字体风格不统一」的视觉错位。所有数值单位为像素（px），字体为
> 当前 Qt 应用程序默认字体（受系统/界面语言影响，不硬编码字号）。

## 1. 设计 Token（唯一事实来源）

| Token | 值 | 含义 | 代码位置 |
| --- | --- | --- | --- |
| `CONTENT_MARGIN` | 12 | 每页内容与页边缘的内边距（四边一致） | `*_page` 的 `setContentsMargins(12,12,12,12)` |
| `GAP_LABEL_TO_CONTROL` | 8 | 字段标题（label）与其控件（input）之间的**垂直**间距 | 各页 `QVBoxLayout.setSpacing(8)` |
| `GAP_BETWEEN_GROUPS` | 8 | 相邻「label→控件」字段组之间的垂直间距（当前与 label→control 间距统一） | 同上 |
| `GAP_CONTROL_ROW_H` | 6 | 同一行内多个控件/文字之间的**水平**间距（如「端口」标签+SpinBox、token 输入框+重置按钮） | `host_port_row` / `rpc_token_row` / `report_port_row` 的 `setSpacing(6)` |
| `CONTROL_HEIGHT` | 原生默认 | 所有可输入/选择控件的高度，由 Qt 风格引擎按字体度量给出 | 不调用 `setMinimumHeight`/`setFixedHeight` |
| `LABEL_HEIGHT` | 原生默认 | 标签高度 = 字体行高，随字号自适配 | 不调用 `setMinimumHeight`/`setFixedHeight` |
| `LABEL_FONT` | 应用默认字体 | 普通字段标题字号 | `_lbl()` 不调用 `setFont` |
| `LABEL_REQUIRED` | 粗体 + `#b00020` | 必填字段标题样式（如「分机号（必填）」） | `ext_label.setStyleSheet("font-weight: bold; color: #b00020;")` |
| `LABEL_SECTION` | 默认 + 2pt | 仪表盘统计分组标题（非设置页表单，仅记录基准） | `app.py:277-280` 的 `+2` 字号 |

## 2. 控件高度规则

1. **统一原生高度**：`QLineEdit` / `QSpinBox` / `QComboBox` 以及与它们同一行的
   按钮（如 RPC token 的「重置」按钮）**不得**调用 `setMinimumHeight(...)` /
   `setFixedHeight(...)`。原生高度天然一致——以电话汇报页「RPC 监听端口」
   `QSpinBox`（`self.rpc_port`）为正确基准，其余控件必须与之等高。
2. **label 高度**：`QLabel` 不单独设高度，由字号决定；与控件等高无关，仅保证
   同页所有 label 使用同一字体（默认）即可。

## 3. 字体与文案规则

1. 普通字段标题用应用默认字体，不在 `_lbl()` 中 `setFont`。
2. **必填字段**标题：`font-weight: bold; color: #b00020;`（红色加粗），用于
   「分机号（必填）」等强制项，作为唯一的强调样式。
3. 除仪表盘统计分组标题外，不在设置页表单中放大字号或加特殊样式，避免风格漂移。

## 4. 间距规则

1. **label → 控件**：`GAP_LABEL_TO_CONTROL = 8`。各页顶层 `QVBoxLayout` 的
   `setSpacing(8)` 即承担此角色（label 与控件是顺序添加的兄弟 widget）。
2. **控件组之间**：当前通过统一的 `setSpacing(8)` 实现视觉分隔；若未来引入
   更强的分组（如 `QGroupBox` 或额外留白），组间留白仍统一为 8，不得出现
   6/8/12 混用。
3. **同行控件水平间距**：`GAP_CONTROL_ROW_H = 6`，用于标签+输入、输入+按钮的
   同一行布局。
4. **页内边距**：四边均为 `CONTENT_MARGIN = 12`。

## 5. 反例（禁止）

```python
# 错误 1：强制高度，造成比原生控件（rpc_port）更高 → 高度不齐
self.report_extension.setMinimumHeight(30)
self.rpc_token.setMinimumHeight(30)
self.rpc_token_reset_btn.setMinimumHeight(30)

# 错误 2：间距混用，label→control 间距在不同页不一致
al.setSpacing(8)   # SIP 账号页
rp.setSpacing(6)   # 电话汇报页  ← 已统一为 8
```

## 6. 正例（推荐）

```python
# 原生高度 + 统一间距，自动与 RPC 监听端口等高等距
self.rpc_port = QSpinBox()
self.rpc_port.setRange(1, 65535)
self.report_extension = QLineEdit()
self.report_extension.setPlaceholderText(tr("settings.ext.ph"))

rp = QVBoxLayout(report_page)
rp.setContentsMargins(12, 12, 12, 12)   # CONTENT_MARGIN
rp.setSpacing(8)                        # GAP_LABEL_TO_CONTROL / GAP_BETWEEN_GROUPS
```

## 7. 校验

- 设置对话框各页的输入框、数字框、下拉框视觉等高（原生高度）。
- 每页 label 与其控件间距一致（8px），同行控件水平间距一致（6px），页边距一致（12px）。
- 必填字段为红色加粗，其余字段为默认字体，无字号/高度漂移。
- `tests/test_app_smoke.py` 全部通过（高度/间距改动不破坏控件引用）。
