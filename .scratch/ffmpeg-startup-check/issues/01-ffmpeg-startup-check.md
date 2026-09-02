# Issue 01: ffmpeg 启动自检日志

Status: done

实现 spec.md 全部内容：`locate_ffmpeg` + `SipCoreService.start()` 的
ffmpeg 就绪/缺失日志，含 TDD 测试。

## 验收

- `uv run pytest`（在 worktree、主 venv 下）新增测试全绿，基线失败
  （test_pjsua2_backend ×4 + test_sigint_shutdown ×1）不变。
- `mypy src/teleflow` 通过。
- 合回 master 后重新打包 DMG（用户测试的是 /Applications 里的安装版）。

## Comments

- 2026-09-02 code-review(Spec 轴)发现:空白 `ffmpeg_path` 时 locate 不查 PATH
  但日志原因说"PATH 中没有",两处真值判断不一致 → 已修(locate_ffmpeg 对
  输入 strip,空白视同未配置),补测试 test_locate_ffmpeg_treats_whitespace_path_as_unconfigured。
- Standards 轴无硬违规;三条可选建议(locate 返回原因对象、测试 helper、
  SipCoreService 构造参数注入 log)未采纳 —— 接口按交接设计冻结,等真实需求再说。
- 最终:212 passed, mypy clean。
