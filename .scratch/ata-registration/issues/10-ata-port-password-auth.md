# 10 — 接入注册器 + ATA 端口 / 密码（双端口 + 认证 + 全面 ATA 命名）

**What to build:** 把 09 的 `AtaRegistrar` 接入 `SipCoreService`：在本机两个端口上分别运行——`ata_port`（pjsua2，承载 INVITE / 媒体）与 `ata_registrar_port`（侧边注册器，承载 `REGISTER`）。HX4E 配置为「代理服务器 = TeleFlow:ata_port、注册服务器 = TeleFlow:ata_registrar_port」即可。同时把 `gateway_*` 配置字段改名为 `ata_*`、退役 `sip_port`、并以 `ata_password` 作为 `REGISTER` 的 digest 认证密码、`sip_number` 作为期望的注册用户名。

**Blocked by:** 09 — 必须先有 `AtaRegistrar` 组件，接入才有东西可挂。

**Status:** wontfix

**Superseded by:** feature `sip-softphone` — TeleFlow 重新设计为纯 SIP 软电话客户端，不再充当注册服务器 / PBX 网关；`ata_*` 服务端字段与注册器接线将被移除（见 `sip-softphone` 议题 01/02）。

- [ ] `SipCoreService` 在 `ata_port` 启动 pjsua2 后端、在 `ata_registrar_port` 启动 `AtaRegistrar`，两者都向同一 `_dispatch` 回报；`register` / `unregister` 事件驱动「ATA 注册」状态。
- [ ] 新增配置 `ata_registrar_port`（默认如 5080）与既有 `ata_port`（默认 5060，即原 `gateway_port`）；退役 `sip_port`（旧配置含 `sip_port` / `gateway_port` 时迁移到 `ata_port` / `ata_registrar_port`）。
- [ ] `ata_password` 作为 `REGISTER` digest 认证密码传给 `AtaRegistrar`（空 = 不认证）；`sip_number` 作为期望注册用户名（可选校验）。
- [ ] 全面 ATA 命名：`gateway_port→ata_port`、`gateway_password→ata_password`、`EVENT_GATEWAY_REGISTERED→EVENT_ATA_REGISTERED`；设置 UI 标签「网关端口 / 网关密码 / 网关注册」→「ATA 端口 / ATA 密码 / ATA 注册」；仪表盘卡片标题「网关注册」→「ATA 注册」。
- [ ] 测试：集成层验证「注册器收到 REGISTER → 仪表盘显示 Contact → pjsua2 仍处理 INVITE」；旧配置迁移后正常加载。

## 设计决策（已定稿）

- **双端口模型**：`ata_port` 跑 pjsua2（通话 / 媒体），`ata_registrar_port` 跑侧边注册器（注册）。两者独立 UDP socket，互不影响；HX4E 的「代理服务器 / 注册服务器」分别指向这两个端口。
- **全面 ATA 命名**：用户视角「网关」不如「ATA」直观（注册的就是那台 ATA 设备，且 spec 称其为 ATA gateway），故 UI 文案、设置标签、内部标识符、事件名统一改为 ATA。
