# 04 — 实时日志样式增强（颜色区分 + 时间戳 + 日志类型标签）

**Feature:** teleflow-ui-refinement

**Status:** ready-for-agent

**Blocked by:** 01 — Dashboard card-based layout

---

## 目标

当前日志视图使用 `QPlainTextEdit` 纯文本显示，所有日志行无颜色区分。原型图要求日志按类型着色，带时间戳，并具备清晰的视觉层次。

## 原型要求

原型图（第 81-87 行 CSS）定义了日志容器的样式：

- 深色背景（`#0b0d11`），等宽字体
- 每行格式：`时间戳  消息内容`
- 颜色编码：
  - **SIP 日志** — 蓝色（`#7fd1ff`）
  - **Media 日志** — 绿色（`#9be8a0`）
  - **Device 日志** — 黄色/橙色（`#f3c77a`）
  - **Error 日志** — 红色（`#ff5d5d`）
- 固定高度（约 220px），溢出滚动
- 新日志自动滚动到底部

## 实现要点

- 使用 `QTextEdit`（或 `QPlainTextEdit` 配合 HTML）替代纯文本追加
- 日志行通过 `Qt.RichText` 或 `QTextCharFormat` 设置颜色
- 需要保持 2000 行上限（当前 `setMaximumBlockCount(2000)`）
- 或者改用 `QListWidget` 配合自定义委托，每行独立着色
- 建议方案：保留 `QPlainTextEdit`，但通过 `appendHtml()` 方法追加带 `<span style="color:...">` 的 HTML 文本
- 日志着色逻辑需要与 `EventLogger` 配合：`EventLogger` 可以按事件类型添加前缀标签（如 `[SIP]`、`[MEDIA]`、`[DEV]`、`[ERR]`），UI 根据标签着色

## 扩展 EventLogger

当前 `EventLogger` 的 `sink` 接收纯文本字符串。需要扩展为结构化的日志类型：

```python
# 新增日志类型枚举
class LogLineType(enum.Enum):
    SIP = "sip"
    MEDIA = "media"
    DEVICE = "dev"
    ERROR = "err"

# 结构化日志行
@dataclass
class LogLine:
    timestamp: str
    text: str
    line_type: LogLineType
```

## 验收标准

- [ ] 日志面板背景为深色，使用等宽字体
- [ ] SIP 日志显示为蓝色，Media 为绿色，Device 为黄色/橙色，Error 为红色
- [ ] 每行显示时间戳 + 消息内容
- [ ] 新日志自动滚动到底部
- [ ] 日志上限 2000 行，超出后丢弃最早的行
- [ ] 日志面板高度约 220px，可滚动

## 参考

- 原型图：`prototypes/teleflow-ui-prototype.html`（第 81-87 行 CSS，第 187-189 行 `fmtLog`）
- 现有代码：`src/teleflow/logging.py` — `EventLogger`、`LogLevel`
- 现有代码：`src/teleflow/app.py` — `MainWindow.log_view`