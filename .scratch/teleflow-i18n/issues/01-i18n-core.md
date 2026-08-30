# 01: i18n 核心机制 + 语言加载 + `language` 配置项

**What to build:** 让整套应用具备可切换的国际化基础能力——一个不依赖 Qt 的纯 Python 翻译内核，支撑后续所有 UI 文本走 `tr()`，并在运行时动态切换语言。具体交付：

- 新增 `src/teleflow/i18n.py`：
  - `tr(key: str, **kwargs) -> str`：按当前语言返回译文，支持 `{placeholder}` 占位符替换；缺失 key 时回退到 `en`，再缺失则返回 key 本身（永不抛错）。
  - `set_language(lang: str)`：接受 `"en"` / `"zh_CN"` / `"auto"`；`"auto"` 用标准库 `locale` + 环境变量（`LANG`/`LC_ALL`/`LANGUAGE`）判定——中文系统（zh/zh_CN）解析为 `zh_CN`，其余回退 `en`。**不使用 Qt 依赖**，保证无界面测试仍可导入。
  - 变更通知：维护一个回调注册表（`register_on_change(cb)` / 触发），语言切换时通知所有订阅者做 retranslate。
- 新增 `src/teleflow/locales/en.json`（默认/回退，含少量起始 key）与 `src/teleflow/locales/zh_CN.json`（中文权威源）。
- `src/teleflow/config.py` 的 `Settings` 增加字段 `language: str = "auto"`（无 ConfigStore 迁移成本，`ConfigStore` 仅保留已知字段）。

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] `tr()`、`set_language()`、`"auto"` 解析、变更回调注册表均已实现且为纯 Python（无 PyQt 导入）。
- [ ] `en.json` 为回退默认；`zh_CN.json` 已建立；未知 key 回退链为 `zh_CN → en → key`。
- [ ] `Settings.language` 默认 `"auto"`；`ConfigStore` 加载旧配置不报错。
- [ ] 新增单测覆盖：`tr` 正常取值、`set_language("zh_CN")` 切换、`"auto"` 在非中文环境回退 `en`、缺失 key 回退、占位符替换。
