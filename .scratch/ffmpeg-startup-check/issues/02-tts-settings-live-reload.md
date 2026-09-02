# Issue 02: TTS 设置改动无需重启即时生效

Status: done

## 问题

用户在设置对话框填好 ffmpeg 路径保存后,点击汇报仍报错(ffmpeg not
found)。根因:`SipCoreService._tts` 懒构建一次后永久缓存(连同围绕它
构建的 `_conversion_queue`),`_resolve_wav` / `conversion_queue` 都直接
复用旧实例,旧实例烧录的是改动前的 `ffmpeg_path`。交接文档已预判
"RPC 路径需重启"。

## 修复

- `_tts_settings_key(settings)`:TTS 相关设置指纹
  (ffmpeg_path / tts_retry_attempts / tts_cache_ttl_seconds)。
- `SipCoreService._tts_backend(settings)` 取代 `_default_tts`:指纹一致
  → 复用缓存后端;不一致 → 重建(CachingTtsBackend(EdgeTtsBackend)),
  并 shutdown 掉围绕旧后端构建的 conversion queue(SyncConversionQueue
  无 shutdown,用 getattr 探测)。注入的测试后端永不替换。
- `_resolve_wav` 与 `conversion_queue` property 改走 `_tts_backend`。

## 验证

- test_report_controller.py:换 ffmpeg_path 后下一次 start_report 用新
  路径重建;设置不变则复用;注入后端不受影响。
- 214 passed, mypy clean。
