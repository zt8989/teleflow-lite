# 03: TTS 语音缓存层

**What to build:** 同一段文字（含音色）首次合成后缓存为 8k mono wav；再次请求同文本直接复用缓存文件，不重复调用 edge-tts/ffmpeg；文本或音色变化（哈希不同）时才重新生成。IVR 每次呼入都播欢迎语与菜单，缓存避免重复合成开销。

**Blocked by:** None (can start immediately)

**Status:** done

- [ ] 新增缓存层（包装 `TtsBackend` 协议），缓存键 = `hash(clean_markdown(text) + voice)`，落在现有 reports 缓存目录（`DEFAULT_CACHE_DIR`）。
- [ ] 命中缓存：缓存文件存在且文本/音色未变 → 直接返回该 wav，不调 `synthesize`/`transcode`。
- [ ] 未命中 / 文本或音色变化：合成 + 转码并写缓存（文件名含哈希，避免覆盖）。
- [ ] 与 `FakeTtsBackend` 兼容（测试可注入假后端，无需真实缓存）。
- [ ] 单元覆盖：缓存命中（不重复合成）、变化重生成、不同 voice 各自缓存。
