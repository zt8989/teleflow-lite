# 08: RPC 重播 IVR 数字菜单（返回上层菜单后可继续操作）

**What to build:** 新增一个 RPC 命令，让数字键 hook（或外部控制）能把当前呼入呼叫「返回上层菜单」——即重新播报 `1~9~0` 的 `ivr_digit_text` 提示音，并重新进入监听按键状态，使用户在听完某条子消息（如天气）后能继续按其他数字键操作。`SipCoreService` 暴露 `replay_ivr_menu(call_id)`，并新增 `POST /v1/ivr/replay` 端点，经 `scheduler` 编排到 Qt 主线程。

**Blocked by:** 05 — 呼入 IVR 编排, 06 — hook 接线, 07 — RPC 播放到通话中(call)

**Status:** done

- [ ] 抽取 `_build_ivr_digit_queue(settings)` 之类的辅助（复用 `_maybe_start_ivr` 中的合成逻辑），仅重新合成 `1~9~0` 的 `ivr_digit_text`（空文本跳过）。欢迎语是否一并重播设为可选/可配置，本期默认只重播数字提示音。
- [ ] `SipCoreService` 新增 `replay_ivr_menu(call_id)`：仅当 `self._ivr_active` 且 `call_id == self._ivr_call_id` 且呼叫仍处于通话中时生效；重置 `_ivr_digit_fired = False`、重建数字消息队列、调用 `_ivr_play_next()` 重新播报；播报结束后由既有 `_on_ivr_playback_done` 把 `_ivr_listening` 重新置为 True，恢复监听。对「媒体尚未 ACTIVE」的窗口同样需具备健壮性（参考 ticket 07 的重试策略）。
- [ ] `rpc.py` 新增 `POST /v1/ivr/replay`：body `{"call_id"}`，镜像 `/v1/report` 的 Bearer 鉴权与错误处理；`call_id` 缺失/非活动 IVR 呼叫分别返回 400/404；经 `scheduler` 编排到主线程；成功返回 202 `{"call_id"}`。
- [ ] 组合用法（说明性，非必做代码）：数字键 `1` 的 `ivr_digit_hook` 可写成「先 `POST /v1/play` 播报天气（见 ticket 07），再 `POST /v1/ivr/replay` 返回菜单」，从而让用户听完天气后继续按 `2`/`3`… 操作。本 ticket 只负责「返回菜单 + 重播数字提示」这一段。
- [ ] 单元覆盖：用 `FakeSipBackend` + `FakeTtsBackend` 验证 (a) 首次菜单播完后按 `1` 触发后，`replay_ivr_menu` 能重建队列并重新进入监听、(b) 非 IVR / 非活动 call_id 被拒绝、(c) `POST /v1/ivr/replay` 经 scheduler 正确转发、(d) 重复调用不叠加多条播放（重置而非追加）。

## Comments

- 2026-08-29: 由用户请求创建。意图：按下一个数字键播完子消息（如天气）后，可「返回上一个菜单」让 `1~9~0` 数字提示继续播放，用户便能继续操作其他数字键。当前实现在 `_on_dtmf` 后把 `_ivr_listening` 置 False 且 `_ivr_digit_fired` 置 True（`sip.py:590-591`），之后不再监听，所以本 ticket 通过重置这两个标志并重播队列来恢复菜单。仅重播数字提示音（不含欢迎语）为本期默认；是否需要把欢迎语也纳入重播待定。
- 2026-08-29: 已实现。`SipCoreService.replay_ivr_menu` + `POST /v1/ivr/replay`（非活动 IVR 呼叫 → 404；成功 → 202 `{"call_id"}`）。重播只重建 `1~9~0` 的 `ivr_digit_text` 队列、重置 `_ivr_digit_fired`/`_ivr_listening` 并重新播报，播完由 `_on_ivr_playback_done` 恢复监听。单测见 `tests/test_ivr.py` / `tests/test_rpc.py`。
