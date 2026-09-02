# 03 — App 层采用 ResolvedConfig

**What to build:** `build_app` 构建 `ResolvedConfig` 传给 service; 语言
初始化用 `config.language_resolved`。

**Blocked by:** 02

**Status:** done

- [ ] `build_app` 创建 `ResolvedConfig(settings)` 并传入 service
- [ ] 语言初始化改用 `resolved.language_resolved`
- [ ] 214 测试全过,启动/汇报/IVR 全链路正常
