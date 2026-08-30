# 02 — TTS 音色设置：下拉选内置 edge-tts 角色 + 自定义

**Type:** task

**What to build:** 把设置对话框里的 **TTS 音色** 从自由文本框（`QLineEdit`）改为 **下拉框（内置 edge-tts 默认角色）+ "自定义…" 选项** 的组合：下拉列出一组内置常用音色，选中即用；选"自定义…"时再露出一个文本框，可手填任意 edge-tts 音色 ID（兼容未来新增/小语种）。存储值仍为 `settings.tts_voice`（原始音色 ID 字符串，配置 schema **不变**），仅改 UI 录入方式。

**Why:** 现状 `app.py:544` 用 `QLineEdit` + placeholder 让用户"自己背 `zh-CN-XiaoxiaoNeural` 这种 ID"。一旦拼错（大小写/后缀漏 `Neural`），错误直到真正合成时才在电话里暴露（edge-tts 报非法 voice）。普通用户既记不住 ID、也不该记。内置下拉降低门槛、并顺手把常用中文音色（含男女/方言/新闻腔）列出来；"自定义…"保留灵活性，覆盖下拉没收录的音色。

**行为模型（目标）：**
- 下拉内置项用"友好名 + ID"展示，例如 `晓晓（女） — zh-CN-XiaoxiaoNeural`、`云希（男） — zh-CN-YunxiNeural`、`云扬（男·新闻）`、`晓伊（女·川渝）`、`Aria（en-US 女）` 等；末项为 `自定义…`。
- 选中某个内置项 → `tts_voice` 取该 ID；**隐藏**自定义文本框。
- 选中 `自定义…` → 显示文本框，取其输入作为 `tts_voice`；输入为空时保存视为无效（沿用原值或提示）。
- 载入设置时：若 `settings.tts_voice` 命中某内置 ID → 预选该项并隐藏文本框；否则 → 选 `自定义…` 并把当前值填入文本框（兼容历史/手改配置）。
- 保存：`settings.tts_voice = 当前选中的内置 ID 或 自定义文本框内容`（维持原 `text().strip()` 语义）。

**涉及模块：**
- `src/teleflow/app.py` 设置对话框：
  - 删除 `self.tts_voice = QLineEdit()`（`app.py:544-545`）与 `rp.addWidget(self.tts_voice)`（`app.py:569`）；改为 `QComboBox`（内置列表 + `自定义…`）+ 条件显示的 `QLineEdit`（自定义输入）。
  - `_load_settings`（`app.py:644`）：按上面载入逻辑预选下拉 / 填充自定义框。
  - `_save_and_close`（`app.py:673`）：按选中态取值写回 `settings.tts_voice`。
- `src/teleflow/config.py`（或 `tts.py`）：新增常量 `BUILTIN_TTS_VOICES: list[tuple[str, str]]`（友好名, ID），集中在配置旁便于维护；默认 `tts_voice` 仍是 `"zh-CN-XiaoxiaoNeural"`（`config.py:120`），自然命中首项。

**红线：** 仅改 UI 录入；不引入录音/DSP；音色 ID 仍是原字符串，合成的缓存 Key 公式（`sha256(text+"\0"+voice)`）不受影响。

**Open（待 triage 确认）：**
- 内置列表收哪些音色：提案以中文为中心 + 少量英文，约 8~10 项（见下草案）；是否要按"语言分组"或用分隔线。
- 自定义文本框是否做轻量校验（如必须以 `Neural` 结尾 / 非空才允许保存）。

**内置列表草案（提案）：**
| 友好名 | ID |
| --- | --- |
| 晓晓（女） | zh-CN-XiaoxiaoNeural |
| 云希（男） | zh-CN-YunxiNeural |
| 云扬（男·新闻） | zh-CN-YunyangNeural |
| 晓伊（女·川渝） | zh-CN-XiaoyiNeural |
| 云健（男） | zh-CN-YunjianNeural |
| 晓辰（女） | zh-CN-XiaochenNeural |
| 云霞（女） | zh-CN-YunxiaNeural |
| 云野（男） | zh-CN-YunyeNeural |
| Aria（en-US 女） | en-US-AriaNeural |
| Guy（en-US 男） | en-US-GuyNeural |
| 自定义… | （文本框） |

**Blocked by:** None

**Status:** ready-for-agent

- [ ] `config.py`（或 `tts.py`）新增 `BUILTIN_TTS_VOICES` 常量（友好名 + ID 列表），含中文为主、少量英文。
- [ ] `app.py` 设置对话框：`tts_voice` 由 `QLineEdit` 改为 `QComboBox`（内置项 + `自定义…`）+ 条件 `QLineEdit`。
- [ ] 选中内置项时隐藏自定义框并取该 ID；选 `自定义…` 时显示框并取其内容。
- [ ] `_load_settings`：命中内置 → 预选并隐藏框；否则选 `自定义…` 并回填原值。
- [ ] `_save_and_close`：按选中态写回 `settings.tts_voice`（保持 `strip()` 语义）。
- [ ] 可选：自定义输入轻量校验（非空 / 形态），保存时拦截无效。
- [ ] 单元/UI 覆盖：载入内置值预选正确；载入未知值落到 `自定义…` 并回填；保存取值正确；默认 `zh-CN-XiaoxiaoNeural` 命中首项。
