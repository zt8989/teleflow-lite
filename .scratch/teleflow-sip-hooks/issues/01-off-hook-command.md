# 01 — 摘机 hook（含共享 HookRunner 与配置）

**What to build:** 当物理座机打入、当前 SIP 自动接通的瞬间（即 `CALL_CONNECTED` 事件触发时），执行用户在设置中配置的「摘机命令」外部脚本/命令，并把 `call_id` 作为参数传入。本票同时交付后续挂机 hook 复用的共享机制：一个可注入的 `HookRunner`（真实实现 `SubprocessHookRunner` 在后台线程中以非阻塞方式执行命令、替换 `{call_id}` 占位符、把执行与错误记入实时日志、吞掉异常以免阻塞通话流程），`Settings` 新增 `off_hook_cmd` 字段并由 `ConfigStore` 持久化，设置弹窗新增「摘机命令」输入框，`build_app` 注入 runner 并在 `CALL_CONNECTED` 上订阅触发。

**Blocked by:** None — can start immediately.

**Status:** resolved

- [ ] 设置弹窗可填写「摘机命令」，保存后写入配置文件，重启应用仍保留。
- [ ] 物理座机打入、当前 SIP 自动接通时，该命令被实际执行，且收到 `call_id` 参数（占位符 `{call_id}` 被正确替换）。
- [ ] 命令执行失败（不存在/非零退出）不会阻塞或中断通话流程，错误会显示在实时日志中。
- [ ] 单元覆盖：`{call_id}` 占位符替换、后台线程非阻塞执行、异常被吞掉不向上抛出。
- [ ] `SubprocessHookRunner` 通过 `HookRunner` 协议注入，`SipCoreService` 本身不依赖 runner，保持可单测（用 `FakeHookRunner` 验证摘机在 `CALL_CONNECTED` 时触发）。
