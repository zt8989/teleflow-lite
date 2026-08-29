# 07: RPC 播放到通话中的呼入（数字键 hook 回放语音）

**What to build:** 让 `1234567890` 这些数字键的 per-digit hook（`ivr_digit_hook`）能通过本地命令，向「正在接通 / 通话中」的呼入呼叫播放语音（TTS 合成或指定 WAV）。即在 `SipCoreService` 暴露一个公开的 `play_to_call`，并新增 `POST /v1/play` RPC 端点。**前提是存在一条正在接通的活动呼叫**；若没有正在接通的电话（call_id 无效 / 呼叫已结束 / 不存在），直接返回错误，不做静默播放。

**Blocked by:** 04 — 后端 DTMF 与播放, 05 — RPC 控制通道(report/status), 06 — hook 接线

**Status:** done

- [ ] `SipCoreService` 新增公开方法 `play_to_call(call_id, *, text=None, audio_path=None, voice=None, hangup_on_eof=False)`：复用 `self._tts`（`CachingTtsBackend`）与现有 `_resolve_wav` 类路径把 text→WAV，或直接使用 `audio_path`；最终调用 `self._backend.play_file_to_call`。`text` 与 `audio_path` 至少其一必填。**前置校验**：必须存在一条「正在接通」的活动呼入（`self._ivr_active and call_id == self._ivr_call_id`，或等价地后端当前活动 call 集合包含该 call_id）；若没有正在接通的电话，直接抛错（如 `ValueError("no active call")`）交由 RPC 层转为 404，绝不向不存在/已结束的 call 静默播放。返回是否真正入队播放。
- [ ] `rpc.py` 新增 `POST /v1/play`，镜像 `/v1/report` 的鉴权（`Authorization: Bearer <token>`）与错误处理：body `{"call_id", "text"?, "audio_path"?, "voice"?, "hangup_on_eof"?}`；经 `scheduler` 编排到 Qt 主线程（pjsua2 非线程安全）。错误码：`call_id` 缺失/格式非法 → 400；`text` 与 `audio_path` 同时缺失 → 400；**没有正在接通的活动呼叫（call_id 不匹配任何活动呼入）→ 404 `{"error":"no active call"}`**；其他 `play_to_call` 抛错 → 400。成功返回 202 `{"call_id"}`。
- [ ] 健壮性 / 错误可见性（对应「正在接通」）：本场景恒为一条已接通的活动呼入，因此**不需要**「轮询重试直到媒体 ACTIVE」，也不必做 183 早期媒体（early media）。但 `pjsua2_backend.play_file_to_call` 当前在 `media_index is None`（媒体未 ACTIVE）时静默 `return`（`pjsua2_backend.py:455-456`），会掩盖失败——应将其改为返回布尔或抛错，使「活动呼叫存在却无法播放」的情况能向上层返回错误，而非伪装成功。前置的活动呼叫校验（见上两条）已覆盖「没有正在接通的电话」这一主路径。
- [ ] 安全：`{call_id}` 来自网关 INVITE（不可信输入，有 shell 注入面），`play_to_call` 必须先确认该 call_id 当前确实是一个活动呼入再播放；RPC 端点必须要求 bearer token（与现有一致），防止未授权方对任意 call 注入音频。
- [ ] 单元覆盖：用 `FakeSipBackend`（记录 `play_file_to_call` 调用）+ `FakeTtsBackend` 验证 (a) 存在活动呼入时 `play_to_call` 合成文本并入队播放、(b) 缺 `call_id` / 缺 text+audio_path 返回 400、(c) **无活动呼入（call 已结束或 call_id 不存在）时 `POST /v1/play` 返回 404**、(d) `POST /v1/play` 经 `scheduler` 正确转发。

## Comments

- 2026-08-29: 由用户请求创建。意图是数字键 hook 回调（如 `curl -X POST .../v1/play -d '{"call_id":"{call_id}","text":"..."}'`）能即时回放提示音。
- 2026-08-29（补充）: 用户明确——"播放到未接通的电话"这一场景不存在，因为其场景恒为一条「正在接通」的电话；**若没有正在接通的活动呼叫，直接返回错误**。据此本期移除「重试直到媒体 ACTIVE」与 183 早期媒体方案，改为以「活动呼叫前置校验 + 无活动呼叫返回 404」为核心，并把后端静默 no-op 改为可返回错误。
- 2026-08-29: 已实现。`SipCoreService.play_to_call` + `POST /v1/play`（无活动呼叫 → 404 `no active call`；缺 text+audio → 400）；`play_file_to_call` 现返回 `bool`，媒体未就绪时 `play_to_call` 抛 `RuntimeError` 上抛为 400，不再静默成功。单测见 `tests/test_ivr.py` / `tests/test_rpc.py`。
