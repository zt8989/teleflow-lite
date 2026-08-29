# 06: Hook 接线（挂机带末次按键 + 每键命令）

**What to build:** 挂机命令 `on_hook_cmd` 现在能接收 `{last_digit}` 占位符；每个数字键被按下时运行该键的 `ivr_digit_hook` 命令（带 `{call_id}` / `{digit}`）。复用现有 `HookRunner` / `SubprocessHookRunner`，`build_app` 注入方式不变。

**Blocked by:** 02 — IVR 配置 schema, 05 — 呼入 IVR 编排

**Status:** done

- [ ] `attach_hooks` 在 `CALL_ENDED` 把 `{last_digit}` 注入 `on_hook_cmd`（未按键传空串）。
- [ ] 订阅 `EVENT_IVR_DIGIT`：`runner.run(ivr_digit_hook.get(digit, ""), {"call_id": call_id, "digit": digit})`；命令为空串则不执行。
- [ ] `build_app` 复用现有 `HookRunner` 注入，无需改动接线形态。
- [ ] 单元覆盖：`{last_digit}` 正确注入挂机 hook、每键命令触发与空串跳过（用 `FakeHookRunner` + `FakeSipBackend` 模拟来电→按键→挂机）。
