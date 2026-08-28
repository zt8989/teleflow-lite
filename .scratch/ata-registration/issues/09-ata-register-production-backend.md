# 09 — ATA 注册服务器（侧边注册器，承接 FreeSWITCH 注册角色）

**What to build:** HX4E/MX8A 等 ATA 必须向 SIP 注册服务器注册——其 SIP 配置只有「注册服务器 / 代理服务器」且有 3 种注册方式（按线路 / 按网关 / 每线认证整体），**无纯 peer 模式**（已查手册确认）。因此 TeleFlow 必须作为注册服务器接受 `REGISTER`。但 pjsua2 2.17 的 Python 绑定既不能回调也不能自动应答 `REGISTER`（已两个实验验证），故新增一个轻量、零依赖的纯 Python UDP「侧边注册器」组件 `AtaRegistrar`：专门应答 `REGISTER`（含 digest 认证）、提取并保存 Contact、向 SIP 核心回报注册事件。通话 / 媒体仍由 pjsua2 负责（见 10 的双端口模型）。

**Blocked by:** None — pjsua2 现已可在 `.venv` 构建；注册器本身不依赖 pjsua2，可独立单测。

**Status:** wontfix

**Superseded by:** feature `sip-softphone` — TeleFlow 重新设计为纯 SIP 软电话客户端，不再充当注册服务器 / PBX 网关，故 `AtaRegistrar` 及其接线将被移除（见 `sip-softphone` 议题 01）。

- [ ] `AtaRegistrar` 监听独立 UDP 端口，收到 ATA 的 `REGISTER` 回 `200 OK`（含正确 Via/From/To/CSeq/Contact/Expires，按需补 `received`/`rport`）。
- [ ] 提取并保存 Contact 与 AOR（用户名），调用 `handler("register", {"contact": ..., "username": ...})`；`SipCoreService` 据此存储并 emit `EVENT_ATA_REGISTERED`，仪表盘「ATA 注册」显示真实 Contact。
- [ ] 支持 SIP digest 认证：首包回 `401 + WWW-Authenticate`，密码正确回 `200 OK`，错误 / 缺失（当已配置密码时）回 `401/403`。密码为空时直接接受。
- [ ] 收到 `Expires: 0`（注销）触发 `handler("unregister", ...)` 并复位状态。
- [ ] 测试（裸 UDP 发 `REGISTER`，无需 pjsua2）：无密码 → 200；有密码 → 401 → 带 `Authorization` 的 200；错误密码 → 被拒；`Expires:0` → unregister。

## 实现提示

- 组件纯 Python（`src/teleflow/ata_registrar.py`），不 `import pjsua2`，故可在无原生库环境单测。
- digest 采用 RFC 2617（无 qop）以最大兼容 HX4E：`HA1=md5(user:realm:pass)`、`HA2=md5(REGISTER:uri)`、`response=md5(HA1:nonce:HA2)`。
- 与 10 配合：10 负责把该组件接入 `SipCoreService`（在 `ata_registrar_port` 上启动）、补 `ata_registrar_port/ata_password/sip_number` 配置、以及「ATA 注册」UI 文案与全面 ATA 命名。
