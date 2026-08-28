# 05 — UI：SIP 账号与注册状态

**What to build:** 设置弹窗可填写 SIP 服务器 / 用户名 / 密码；仪表盘显示 SIP 注册状态（已注册到服务器 / 未注册）。对用户而言：像 MicroSIP 一样配置自己的 SIP 账号并一眼看到是否在线。

**Blocked by:** 04 — SipCoreService 作为软电话（需要 `EVENT_SIP_REGISTERED` 状态）。

**Status:** ready-for-agent

- [ ] 设置弹窗显示 SIP 服务器 / 用户名 / 密码（密码掩码）字段，取代原 ATA 端口 / 密码字段。
- [ ] 仪表盘卡片「SIP 注册」显示状态；接到 `EVENT_SIP_REGISTERED` 更新；注册失败有可见提示。
- [ ] 注册状态变化写入实时日志（如 `SIP registered: ...` / `SIP registration failed`）。
- [ ] 冒烟测试覆盖仪表盘状态随注册事件变化。
