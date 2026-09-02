# Spec: ResolvedConfig 计算值代理

## 背景

"计算值"散落在各处：`locate_ffmpeg` 在 tts.py 和 sip.py 各调一次,
`resolve_report_target` 是 sip.py 的自由函数,`language="auto"` 的
解析藏在 app.py 启动逻辑里。每个消费者各自 resolve,逻辑重复且难以
追踪"空值时应该怎么算"。

## 需求

新建 `config.ResolvedConfig(Settings)`,持有 Settings 快照,暴露计算
属性: `ffmpeg_bin`、`language_resolved`、`report_target`。消费者不再
自行 resolve,统一通过 ResolvedConfig 获取。

## 非目标

- 不改变 Settings 的序列化/持久化行为(ConfigStore 照旧)。
- 不改 Settings 本身为 dataclass+property(保持纯数据)。
