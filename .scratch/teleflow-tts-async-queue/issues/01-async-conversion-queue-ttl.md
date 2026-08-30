# 01 — 统一音频转换入口：后台队列异步执行 + 缓存 TTL

**Type:** task

**What to build:** 把全部"文本 → 8k mono WAV"的音频转换收拢到**唯一入口**，并以**后台队列异步**方式执行；缓存层增加 **TTL 过期**语义。具体交付：

1. 一个统一转换入口：所有调用方只调 `tts.synthesize_to_wav(text, voice, *, prefix)`，由 `CachingTtsBackend` 内部完成"缓存命中（含 TTL）/未命中 → 合成 + 转码"。**删除**现有三处重复逻辑（`sip.py:_resolve_wav` 的内联 `synthesize`+`transcode` 与非缓存分支、`app.py:_synthesize` 直接 `synthesize`+`transcode`）。
2. 一个后台 `ConversionQueue(backend, max_workers=N)`：内部用**有界 worker 池**（默认 4）真正**并行**跑转换（同时限制并发 ffmpeg 数）。`submit(text, voice, *, prefix, on_done, order=None)` 立即返回，worker 后台跑转换并经 `on_done(path)` 回调交付；回调跨回 GUI 线程走既有 `gui()`/`_defer`，与 hangup 一致。
3. 缓存 TTL：`CachingTtsBackend` 以 WAV 文件 mtime 判定新鲜度，`now - mtime <= tts_cache_ttl_seconds` 才命中，否则重渲染；TTL 经配置 `tts_cache_ttl_seconds`（默认 604800，运行时读取）可调，可选启动扫描清理超期孤儿 WAV。
4. **缓存 Key（硬性）：`key = sha256( clean_markdown(text) + "\0" + voice )[:16]`** —— 即**文字 + 当前语音角色**哈希。"当前语音角色"指调用时的 `voice`（如 `settings.tts_voice`），**必须**纳入 Key，使不同角色的同款文字落不同缓存、互不串扰。`prefix`（`ivr_`/`report_` 等）仅作文件名命名空间（`{prefix}_{key}.wav`），**不**参与哈希。现有 `CachingTtsBackend._cache_key`（`tts.py:191`）已按此实现，本 Ticket 保持并锁定该公式，TTL 以该 Key 对应 WAV 的 mtime 为准。

**Why:** 当前三处调用点各自重写了"合成 + 转码 + 命中日志"逻辑，且都是**同步**的：
- IVR 在入站呼入的 SIP 事件线程上**顺序**合成欢迎语 + 最多 9 段数字菜单（`sip.py:637` / `sip.py:650`），最多 10 次 edge-tts + ffmpeg 渲染全部完成才开始首个播放；任一段网络慢都会拖住入站自动接听与首个提示播放。
- 汇报/试听路径 `_resolve_wav`（`sip.py:522`）为 `FakeTtsBackend` 留了一条**不走缓存**的 `synthesize`+`transcode` 分支，缓存与命中/未命中逻辑散落在 wrapper 与 `_resolve_wav` 两处。
- app 试听 `app.py:_synthesize`（`app.py:903`）虽已丢到 `threading.Thread`，却直接调 `synthesize`+`transcode`、**绕开缓存**，每次都重渲染，且不吃统一的命中/未命中日志。

此外缓存**没有 TTL**：`CachingTtsBackend.synthesize_to_wav` 只判 `wav_path.exists()`，条目永不过期，长时间运行后既无刷新也无界增长。

**行为模型（目标）：**
- 调用方永远只写 `self._conversion_queue.submit(text, voice, prefix=..., on_done=cb)`，不再直接碰 `synthesize`/`transcode`。
- 缓存命中且未超 TTL → 不触达 inner backend，立即 `on_done(已有 wav)`；超 TTL 或缺失 → 后台渲染后 `on_done`。
- IVR：入站时**一次性并行提交全部提示**（欢迎语 + 1~9~0 数字菜单）到队列，每段带 `order` 播放序号；**不是**"转换完一个再转换下一个"的串行阻塞。各自 `on_done` 就绪后按 `order` 序号**逐个播报**，未就绪的等其就绪再播（播放顺序由序号保证，与完成先后无关）；抢断（barge-in）仍按播放队列停播/丢弃未就绪项。
- 汇报：提交转换任务，仅当 `on_done` 拿到 wav 后才 `place_report_call`，命中时近乎即时、未命中时渲染完再外呼。
- 试听（app）：同样走 `submit`，复用缓存与命中/未命中日志，保留 watchdog + `_synth_done` 信号。

**涉及模块：**
- `src/teleflow/tts.py`：
  - `CachingTtsBackend.synthesize_to_wav` 增加 TTL（mtime 新鲜度判定）；保留 `synthesize`/`transcode` 委托（供 `TtsBackend` 协议与测试）。
  - 新增 `ConversionQueue`：`submit(text, voice, *, prefix, on_done)` + 单 worker FIFO 守护线程；构造时持有 `TtsBackend`（通常为 `CachingTtsBackend`）。
  - 新增配置读取 `tts_cache_ttl_seconds`（默认 604800），并在 `ConfigStore`/`Settings` 持久化（可选：Settings 对话框加一项）。
- `src/teleflow/sip.py`：
  - `SipCoreService` 持有一个共享 `ConversionQueue`（随 `self._tts` 构造）。
  - `_maybe_start_ivr` / `_build_ivr_digit_queue` 改为提交转换任务、按就绪入播放队列（不在呼入线程同步渲染）。
  - `_resolve_wav` 收敛为"提交任务 + 在 `on_done` 中继续"（汇报路径）；删除非缓存分支与内联 `synthesize`+`transcode`。
- `src/teleflow/app.py`：`_synthesize`（试听）改为 `self._conversion_queue.submit(...)`，删除直接 `synthesize`+`transcode`。
- 测试 `tests/test_tts.py`：新增 TTL 过期强制重渲染、TTL 内命中复用、队列 `on_done` 异步返回且按序交付等用例；`sip.py`/IVR、汇报、试听相关测试注入 `FakeTtsBackend` + 必要时 `FakeConversionQueue` 以无网络/无硬件跑通。

**红线：** 仅 TTS 合成 WAV 是既被特例允许的转换；异步/队列化**不**引入任何直播音频录制或 DSP。

**Open（待 triage 确认）：**
- TTL 默认时长：提案 7 天（604800s），可经 `tts_cache_ttl_seconds` 调。
- 是否做启动扫描清理超期孤儿 WAV：提案"做，限制磁盘"。
- 队列并发度：`max_workers` 默认 4（有界并行，限制并发 ffmpeg）；可按机器/网络调。
- IVR 消费模型（已确认）：**一次性并行提交全部提示 + 按 `order` 序号逐个播报**，非串行"转换一个播一个"。

**Blocked by:** None

**Status:** ready-for-agent

**实施范围决策（与原始描述的分歧，已落地）：** 后台 `ConversionQueue` 异步只应用于 **IVR 菜单**（`_ivr_begin` 并行提交全部提示并 `on_done` 回调按序播报）与 **app 试听**（`app.py:_synthesize` 已走 `synthesize_to_wav` 统一入口）。**汇报（report）路径保持同步**：`start_report` → `_resolve_wav` 仍**同步**调用统一的 `tts.synthesize_to_wav`，但走的是带缓存 + TTL 的统一入口（不再有"不走缓存"的内联 `synthesize`+`transcode` 分支）。原因：RPC 层 `rpc.py` 与 `test_report_controller.py::test_tts_failure_reports_failed`（`pytest.raises(TtsError)`）要求 `start_report` 在合成失败时**同步抛错/即时返回 HTTP 错误**，无法改成异步回调模型；同理 report 必须"先拿到 WAV 再 `place_report_call`"。因此 report 复用统一缓存 + TTL 入口，但不在队列里异步化。该范围已通过现有 RPC / report 测试守住。

- [ ] `CachingTtsBackend.synthesize_to_wav` 增加 TTL：以 WAV mtime 判定 `now - mtime <= ttl`；超期按未命中重渲染；保留 `[TTS] 缓存命中/未命中` 日志。
- [ ] 缓存 Key 锁定为 `sha256( clean_markdown(text) + "\0" + voice )[:16]`（文字 + 当前语音角色哈希）；`prefix` 仅作文件名前缀、不参与哈希；不同 voice 的同文字互不串扰。
- [ ] 新增 `ConversionQueue(backend, max_workers=4)`：有界 worker 池**并行**转换，`submit(text, voice, *, prefix, on_done, order=None)` 异步交付，`on_done` 跨回 GUI 线程走 `_defer`/`gui()`。
- [ ] 配置 `tts_cache_ttl_seconds`（默认 604800）持久化于 `ConfigStore`/`Settings`，运行时读取；可选启动扫描清理超期 WAV。
- [ ] `SipCoreService` 持有共享 `ConversionQueue`（随 `self._tts` 构造）。
- [ ] IVR：入站时**一次性并行提交**欢迎语 + 1~9~0 全部提示（带 `order` 序号），不在呼入线程同步串行渲染；各自 `on_done` 就绪后按 `order` 逐个播报，未就绪等待；barge-in 行为不变。
- [ ] `_resolve_wav` 收敛为提交任务 + `on_done` 继续；删除非缓存分支与内联 `synthesize`+`transcode`。
- [ ] `app.py:_synthesize` 改走 `submit`；删除直接 `synthesize`+`transcode`；保留 watchdog + `_synth_done`。
- [ ] 测试：TTL 过期强制重渲染 / TTL 内命中 / 队列异步按序交付；IVR、汇报、试听测试注入 fake 后端 + 必要时 `FakeConversionQueue`。
- [ ] 红线断言：异步化后仍无录音/DSP；仅 TTS 合成 WAV。
