# 01 — 移除 PBX 注册器（回退 09/10/11）

**What to build:** TeleFlow 不再充当 SIP 注册服务器 / PBX 网关。删除刚刚实现的 `AtaRegistrar` 及其接线，TeleFlow 不再为 ATA 的 `REGISTER` 打开任何监听套接字。对用户而言：启动 TeleFlow 后不再有「等待 ATA 注册」的服务器角色，「ATA 注册」这一概念整体消失。本票是后续「纯 SIP 软电话」重设计的清理前提。

**Blocked by:** None — 可立即开始。

**Status:** ready-for-agent

- [ ] 删除 `AtaRegistrar` 模块；`sip.py` / `app.py` 中不再有任何对它的 import 或构造。
- [ ] `SipCoreService` 不再启动 / 接受注册器；`build_app` 不再构造 `AtaRegistrar()`。
- [ ] 仪表盘「ATA 注册」卡片与设置中的 ATA 端口 / ATA 密码字段移除。
- [ ] 注册器原始 UDP 单测删除；全量测试套件保持绿色。
- [ ] 议题 09 / 10 / 11 标记为被本重设计取代（superseded）。
