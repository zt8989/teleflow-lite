# 03 — pjsua2 作为客户端注册

**What to build:** 启动后 TeleFlow 作为 SIP 客户端向配置的注册服务器注册，并上报「已注册 / 未注册 / 注册失败」状态。对用户而言：填入 SIP 账号后点启动，TeleFlow 会真正注册上线（而非停留在本地对等体）。

**Blocked by:** 02 — SIP 客户端配置（需要 `sip_server` / `sip_user` / `sip_password` 字段）。

**Status:** ready-for-agent

- [ ] `Pjsua2Backend` 创建的 `Account` 向 `sip_server` 注册，携带 `sip_user` / `sip_password`（`regConfig` + `authCreds`），取代原先静态的 `sip:teleflow@localhost` 对等账号。
- [ ] 后端把注册成功 / 失败 / 注销上报给 service 的 `handler`（新增 `register` / `unregister` / `register_failed` 原始事件）。
- [ ] 注册失败时状态被正确反映（不能静默显示为在线）。
- [ ] 相应后端单测（或 fake 后端）覆盖注册成功与失败路径。
