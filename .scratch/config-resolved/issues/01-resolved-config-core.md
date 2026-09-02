# 01 — ResolvedConfig 核心类 + ffmpeg_bin 属性

**What to build:** 新建 `config.ResolvedConfig`,持有 Settings 快照,
暴露 `ffmpeg_bin -> str | None` 和 `language_resolved -> str` 两个计算
属性。纯新增,不改任何消费方。

**Blocked by:** None — can start immediately.

**Status:** done

- [ ] `ResolvedConfig.__init__(settings: Settings)` 保存快照
- [ ] `ffmpeg_bin`: 空→locate_ffmpeg; 非空→检查文件存在再返回或 None
- [ ] `language_resolved`: "auto"→系统语言; 其他→原样返回
- [ ] 测试覆盖:空路径/非空路径/PATH fallback/auto语言/固定语言
