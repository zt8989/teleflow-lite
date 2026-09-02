# Issue 01: ffmpeg 启动自检日志

Status: in-progress

实现 spec.md 全部内容：`locate_ffmpeg` + `SipCoreService.start()` 的
ffmpeg 就绪/缺失日志，含 TDD 测试。

## 验收

- `uv run pytest`（在 worktree、主 venv 下）新增测试全绿，基线失败
  （test_pjsua2_backend ×4 + test_sigint_shutdown ×1）不变。
- `mypy src/teleflow` 通过。
- 合回 master 后重新打包 DMG（用户测试的是 /Applications 里的安装版）。
