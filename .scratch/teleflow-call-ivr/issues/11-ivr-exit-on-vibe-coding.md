# 11: 按 0 开始 Vibe Coding 后 WorkBuddy 未检测到语音（IVR 麦克风被压制）

**What to build:** 修复「按 0 开始 Vibe Coding 后，WorkBuddy 未检测到语音」。根因：IVR 模式下 `_is_ivr` 会**抑制麦克风桥接**（只做单向欢迎/菜单播放），所以通话上行（ caller 语音）被静音，按下 0 启动 Vibe Coding（发 Ctrl+D）后，通话仍是单向，WorkBuddy 听不到用户声音。修复：按「开始 Vibe Coding」的数字键（约定为 0，与 on-hook 守卫同源）时退出 IVR 菜单模式，把通话恢复为正常的**双向桥接**，call 仍保持 CONNECTED，挂机仍正常发 CALL_ENDED（Ctrl+D+Enter 不受影响）。

**Blocked by:** 05 — 呼入 IVR 编排

**Status:** done

- [ ] `pjsua2_backend.unmark_ivr(call_id)`（`# pragma: no cover`）：清除 `call._is_ivr` 并在媒体已 ACTIVE 时立刻建立双向桥接（下行 call→播放设备、上行 采集设备→call）。与 `mark_ivr` 对应。
- [ ] `SipBackend` Protocol 增加 `unmark_ivr`；`FakeSipBackend` 加 `ivr_unmarked` 记录（无真实桥可恢复）。
- [ ] `SipCoreService._on_dtmf`：当 digit == `settings.ivr_exit_digit`（配置项，默认 "0"，**不写死**）时调用新增的 `_exit_ivr_to_call(call_id)`，再 emit `EVENT_IVR_DIGIT`（保证先恢复桥接再发 per-digit hook 的 Ctrl+D）。`_exit_ivr_to_call` 置 `_ivr_active=False`、`_ivr_call_id=None`、清空队列/监听标志，并 `backend.unmark_ivr(call_id)`；**不**改 `_last_digit`、`_state`、`_active_call_id`，所以挂机 Ctrl+D+Enter 仍生效。
- [ ] on-hook 守卫（`attach_hooks._on_hook`）同样改用 `settings.ivr_exit_digit`，不再写死 "0"。
- [ ] `Settings` 新增 `ivr_exit_digit: str = "0"`，`examples/setup_ivr_hooks.py` 设为 "0"；`config.json` 经 setup 重灌。
- [ ] Settings 对话框「呼入 IVR」页数字菜单每行新增第 4 列「退出↔双向桥接」勾选框：勾上表示按该键退出菜单、切回双向桥接；单选（勾一个自动取消其它），存 `ivr_exit_digit`；`_load_settings` 按配置勾选、`_save_and_close` 取唯一勾选项（无则 ""）。单测见 `tests/test_app_smoke.py`（round-trip + 单选）。
- [ ] 单测 `test_pressing_zero_exits_ivr_to_two_way_call`：验证按 0 后 `ivr_unmarked==["C1"]`、`_ivr_active is False`、`active_call_id=="C1"`（通话仍在）、且 per-digit hook 仍触发。

## Comments

- 2026-08-29: 用户反馈按 0 后 WorkBuddy 未检测到语音。确认 IVR 单向模式压制麦克风是 TeleFlow 侧直接原因；按 0 退出 IVR 恢复双向桥接是必要条件。
- 注意（待用户确认）：双向桥接只保证「通话是真正的双向通话」。WorkBuddy 能否真正听到用户，还取决于它监听的音源：(a) 若 WorkBuddy 录 PC 麦克风，则用户应对着电脑说话；(b) 若 WorkBuddy 要听到「座机那头的人声」，当前桥接架构不会把通话上行暴露到任何 PC 可录制设备（上行只进通话、不回流到播放/采集设备），需要额外加一条 monitor 路由（call 上行 → PC 设备），但会有回声/啸叫风险，需用户确认拓扑后再加，避免误改。
