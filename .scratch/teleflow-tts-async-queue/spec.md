# spec — 音频转换统一异步队列 + 缓存 TTL

## 目标
把所有"文本 → 8k mono WAV"的音频转换（edge-tts 合成 + ffmpeg 转码）收敛为**一个**统一入口，并以**后台队列异步**形式执行；缓存增加 **TTL 过期**语义，保证长时间运行后仍能刷新、且磁盘占用有界。

## 范围
- 仅涉及 TTS 合成这一条已被红线特例允许的转换链路（`tts.py` 产出的合成 WAV），**不**触碰任何直播音频录制 / DSP。
- 收敛三类现有调用点：IVR 菜单、汇报/试听播放、app 试听（见 issue 01）。

## 关键设计
1. **统一入口**：所有调用方只调 `tts.synthesize_to_wav(text, voice, *, prefix)`，由 `CachingTtsBackend` 内部完成"缓存命中(含 TTL)/未命中→合成+转码"。删除 `sip.py:_resolve_wav` 与 `app.py:_synthesize` 里重复的 `synthesize`+`transcode` 内联逻辑与非缓存分支。
2. **异步队列**：新增 `ConversionQueue(backend, max_workers=N)`，内部用**有界 worker 池**（默认 `max_workers=4`）真正**并行**跑转换，同时限制并发 ffmpeg 数量。`submit(text, voice, *, prefix, on_done, order=None)` 立即返回；worker 在后台跑 `synthesize_to_wav` 并通过 `on_done(path)` 回调交付。结果回调跨回 GUI 线程走既有 `gui()`/`_defer` 队列（与 hangup 一致）。
   - **IVR 批量并行模型**：入站时**一次性**把全部提示（欢迎语 + 1~9~0 数字菜单）按播放顺序提交进队列（各自带 `order` 序号），而非"转换完一个再转换下一个"的串行阻塞；每段 `on_done` 就绪后按 `order` 序号**逐个播报**，未就绪的等其就绪再播（播放顺序由序号保证，与完成先后无关）。barge-in 仍按播放队列停播 / 丢弃未就绪项。
3. **缓存 TTL**：`CachingTtsBackend` 以 WAV 文件 mtime 记录新鲜度；`now - mtime <= cache_ttl_seconds` 才算命中，否则按未命中重渲染。TTL 通过配置 `tts_cache_ttl_seconds`（默认 7 天 = 604800，运行时读取）可调；可选启动扫描清理超过 TTL 的孤儿 WAV 以限制磁盘。

## 缓存 Key 规则（硬性）
- **Key = 哈希( 文字 + 当前语音角色 )**，`key = sha256( clean_markdown(text) + "\0" + voice )[:16]`。
- "当前语音角色"指调用时的 `voice`（即 `settings.tts_voice` 等当前角色），**必须**纳入 Key，使不同角色的同款文字落不同缓存、互不串扰。
- `prefix`（如 `ivr_`/`report_`）仅作文件名命名空间（`{prefix}_{key}.wav`），**不**参与哈希；同一 (text, voice) 在不同 prefix 下各自存一份。
- 现有 `CachingTtsBackend._cache_key`（`tts.py:191`）已按此实现，本 Ticket 需保持并显式锁定该公式，TTL 以该 Key 对应的 WAV 文件 mtime 为准。

## 红线
仅 TTS 合成 WAV 例外；队列/异步不改变"不录音、不做 DSP"约束。
