# 02: IVR 配置 schema 与设置 UI

**What to build:** 用户能在设置里开启 IVR、填写欢迎语，并为每个数字键 `1-9-0` 分别配置播报词与命令；配置保存后持久化、重启保留。这是后续 IVR 编排与 hook 接线的配置基础。

**Blocked by:** None (can start immediately)

**Status:** done

- [ ] `Settings` 新增 `ivr_enabled: bool = True`、`ivr_welcome: str = ""`、`ivr_digit_text: dict[str, str] = {}`、`ivr_digit_hook: dict[str, str] = {}`（键为数字字符 `"1".."9"`、`"0"`），默认值正确。
- [ ] `ConfigStore` 保存/加载 round-trip 正确：dict 字段 JSON 往返无误、未知键忽略、旧配置缺字段回落默认。
- [ ] 设置弹窗新增「启用 IVR」开关、欢迎语输入框，以及 `1-9-0` 每键的 `text` / `hook` 输入；保存后写入配置文件。
- [ ] 单元覆盖：字段默认值、dict 字段往返、设置 UI 值正确写入 `Settings`。
