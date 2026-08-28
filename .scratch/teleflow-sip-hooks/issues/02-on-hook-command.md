# 02 — 挂机 hook（复用 HookRunner 与配置模式）

**What to build:** 物理座机挂断之后（即 `CALL_ENDED` 事件触发时，覆盖 BYE 与 CANCEL 两种通话结束场景），执行用户配置的「挂机命令」外部脚本/命令，并把 `call_id` 作为参数传入。完全复用 01 交付的 `HookRunner`、配置脚手架（`Settings.on_hook_cmd` + `ConfigStore` 持久化）、订阅接线模式与设置弹窗 UI 模式。

**Blocked by:** 01 — 摘机 hook（含共享 HookRunner 与配置）。

**Status:** resolved

- [ ] 设置弹窗可填写「挂机命令」，保存后写入配置文件，重启应用仍保留。
- [ ] 物理座机挂断（`CALL_ENDED`）后，该命令被实际执行，且收到 `call_id` 参数（`{call_id}` 占位符被正确替换）。
- [ ] 命令执行失败不会阻塞流程，错误显示在实时日志中（复用 01 的 runner 行为）。
- [ ] 单元覆盖：`CALL_ENDED` 触发挂机命令——用 `FakeHookRunner` + `FakeSipBackend` 模拟来电接通后座机发送 BYE，验证挂机命令被执行。
