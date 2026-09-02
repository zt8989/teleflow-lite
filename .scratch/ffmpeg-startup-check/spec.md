# Spec: ffmpeg 启动自检 (ffmpeg-startup-check)

## 背景

打包的 TeleFlow.app 从 Finder/Dock 启动时，进程 PATH 只含系统最小集
（没有 `/opt/homebrew/bin`），导致 edge-tts 转码阶段找不到 ffmpeg。但这个
失败只在换新文案做 TTS 合成时才暴露（`FfmpegNotFound`），用户无法提前
知道是环境问题。

用户需求原话：**"找不到 ffmpeg 应该启动时就在日志中体现"** —— SIP 服务
启动时探测 ffmpeg 可用性并写日志，而不是等第一次合成失败。

## 需求

1. `teleflow.tts` 暴露模块级 `locate_ffmpeg(ffmpeg_path="") -> str | None`：
   配置路径优先（必须是已存在文件），否则查 PATH；找不到返回 `None`。
   `EdgeTtsBackend._ffmpeg_bin` 复用它，行为不变（找不到仍抛 `FfmpegNotFound`）。
2. `SipCoreService.start()` 在加载配置后写一条 ffmpeg 就绪日志：
   - 找到：`[TTS] ffmpeg 就绪: <路径>`
   - 找不到：`[TTS] 找不到 ffmpeg: <原因>; 新文本合成会失败(缓存音频不受影响); 当前进程 PATH=<PATH>`
     （PATH 是关键诊断信息：GUI 启动的 .app 只拿到最小 PATH）

## 非目标

- 不自动改配置或弹 UI 提示（只写日志）。
- 不触碰看门狗误报 bug（用户明确"先不动"）。
