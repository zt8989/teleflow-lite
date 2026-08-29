# 01 — SIP 端口自动探测 + 强制端口占用提醒

Type: task
Status: resolved
Blocked by:

## 需求

- 默认 `sip_port` 为空:启动时自动探测,从 5060 起找第一个可绑定的 UDP 端口。
- 可强制指定端口(`~/.config/teleflow/config.json` 的 `sip_port`,如 `"5062"`):
  - 指定后启动时检测该端口是否被占用;
  - 被占用 → 提醒用户(托盘通知 + 日志),并自动改用下一个空闲端口。
- 旧配置迁移:配置里遗留的旧默认值 `5060`(int)视为"未指定"(自动),避免老配置永久触发占用提醒;其他显式值(如 `5070`)保留为字符串。
- 文案:界面"SIP 注册"状态卡改为"网关注册";设置对话框删除"SIP 本地端口"输入项。
- 端口探测必须在测试中可注入(不真绑 socket),保证 CI 确定性。

## 验收

- [ ] 空配置启动:探测 5060 → 若占用(模拟),自动选 5061,backend 收到 5061
- [ ] 指定 5060 且被占用:backend 收到 5061,并发出 `sip_port_conflict` 事件
- [ ] 指定 5070 且空闲:backend 收到 5070,无冲突事件
- [ ] config 迁移:旧 `5060`(int)→ 空;`5090`(int)→ `"5090"`
- [ ] UI:设置菜单无端口输入;dashboard 显示"网关注册"
- [ ] 全量 pytest 通过

## Comments

2026-08-29: 创建。根因见 diagnostics 会话(端口冲突导致 408)。

## Answer

2026-08-29 已实现并验证:

- `Settings.sip_port` 改为 `str = ""`(空 = 自动);`ConfigStore.load` 归一化:旧 int 值转 str,遗留默认 `5060` 升级为 `""`(避免老配置钉死与同机 FreeSWITCH 冲突的端口),其他显式值保留。
- `sip.py` 新增 `resolve_sip_port(preferred, probe=...)`:空/无效 → 从 5060 扫描取第一个空闲端口;有效值 → 空闲则用,被占用 → 发 `sip_port_conflict` 事件并自动漂移到下一个空闲端口。`_udp_port_available` 在 Windows 用 `SO_EXCLUSIVEADDRUSE` 探测(普通通配 bind 会与带 SO_REUSEADDR 的具体绑定共存,漏检同机注册器)。
- `app.py`:`SipCoreService.start()` 接入解析;设置对话框删除"SIP 本地端口"输入项(整个"SIP 服务"页);dashboard 状态卡"SIP 注册"→"网关注册";托盘对端口冲突弹警告通知;UI 原型同步。
- 测试:config 迁移/归一化断言更新;`test_sip.py` 新增自动漂移、冲突事件、指定端口通过、真实探测语义测试(autouse fixture 保证探测确定性)。
- 端到端验证(真实 config + pjsua2):旧 `5060` 配置自动迁移为 `""` → 探测跳过被 FreeSWITCH 占用的 5060 → 选 5061 → `registered` 事件。93 个测试通过,mypy 通过。