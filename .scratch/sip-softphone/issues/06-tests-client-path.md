# 06 — 客户端路径测试

**What to build:** 测试覆盖软电话作为客户端向（fake）注册服务器注册、注册状态流入仪表盘与日志的完整路径；删除已废弃的注册器原始 UDP 测试。对用户而言：无直接感知，但保证「纯 SIP 软电话」行为可回归。

**Blocked by:** 04 — SipCoreService 作为软电话, 05 — UI：SIP 账号与注册状态。

**Status:** ready-for-agent

- [ ] fake SIP 后端模拟客户端注册成功 / 失败；service / 仪表盘 / 日志据此反映状态。
- [ ] 删除 `AtaRegistrar` 原始 UDP 测试（`test_ata_registrar.py`）及 `test_sip.py` 中注册器接线测试。
- [ ] 全量测试套件绿色；`EVENT_SIP_REGISTERED` 命名在测试与日志中断言一致。
