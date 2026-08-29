# 10: 挂机未发送 Ctrl+D+Enter（on-hook 守卫需由 App 判定，不能用 shell 测试）

**What to build:** 修复「最后一次按 0 后挂机，没有发送 Ctrl+D+Enter」的问题。按 0 启动 Vibe Coding（per-digit hook `connect` 发 Ctrl+D）后，挂机（CALL_ENDED）应只在「上一次按的是 0」时发 Ctrl+D+Enter（`hangup`）以停止并确认录音。原实现把守卫写进 `on_hook_cmd` 里的 POSIX shell 测试 `[ "{last_digit}" = "0" ] && …`，但在 Windows 上 `subprocess.run(shell=True)` 走的是 `cmd.exe`，`[` 不是命令，测试恒失败、`&&` 短路，导致 hangup 命令从未执行。修复：把「last_digit == "0"」的判定移到 `attach_hooks` 的 Python 代码里（平台无关），`on_hook_cmd` 只保留纯 `hangup` 命令。

**Blocked by:** 06 — hook 接线

**Status:** done

- [ ] `hooks.attach_hooks._on_hook`：仅当 `last_digit == "0"` 时才 `runner.run(on_hook_cmd, …)`；其余情况（无按键 / 按了其它数字）直接返回，不发挂机键。
- [ ] `examples/setup_ivr_hooks.py`：`on_hook_cmd` 改为纯 `hangup` 命令（去掉 `[ … ]` 测试与 `LAST_DIGIT` 占位），守卫语义由 App 负责；同步更新说明注释。
- [ ] 重新应用配置到 `config.json`（`.venv/Scripts/python.exe examples/setup_ivr_hooks.py`）。
- [ ] 单测：新增 `test_on_hook_fires_on_call_ended_when_last_digit_zero`、`test_on_hook_skips_when_last_digit_not_zero`（test_hooks.py），并加固 `test_ivr_only_first_digit_triggers`（0 以外数字不触发 on-hook）；test_ivr.py 另加 `test_on_hook_sends_keys_only_when_last_digit_is_zero`。注意这些测试必须注入 `FakeTtsBackend`，否则 IVR 不激活、`last_digit` 为空，会误判。

## Comments

- 2026-08-29: 用户反馈「按 0 挂机时没有按 Ctrl+D+Enter」。根因确认是 Windows `cmd.exe` 不识别 `[ … ]` shell 测试，而非 `last_digit` 未传（wiring 一向正确，CALL_ENDED 的 kwargs 含 `last_digit`）。守卫下沉到 `attach_hooks` 后，挂机键在 Windows 下也能正常发送。off-hook 仍不发 Ctrl+D；仅按 0 时的 per-digit `connect`（Ctrl+D）与挂机 `hangup`（Ctrl+D+Enter）成对。
