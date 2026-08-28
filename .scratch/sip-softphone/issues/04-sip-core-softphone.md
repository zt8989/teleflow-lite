# 04 — SipCoreService 作为软电话

**What to build:** 服务把 `register` / `unregister` 视为「客户端注册」事件（来自后端的注册回调，而非来自接受服务器侧 REGISTER），保留自动应答 INVITE 并把通话音频桥接到 PC 所选的播放 / 采集设备。对用户而言：一个已注册的软电话，呼入自动接通、声音走选定设备，注册状态可查询。

**Blocked by:** 03 — pjsua2 客户端注册（需要先有后端上报的注册事件语义）。

**Status:** ready-for-agent

- [ ] `SipCoreService._dispatch` 处理来自后端的 `register` / `unregister` / `register_failed`，存储注册状态，emit `EVENT_SIP_REGISTERED`（取代 `EVENT_ATA_REGISTERED`）。
- [ ] INVITE 自动应答与音频路由逻辑保持不变。
- [ ] `place_call` 在已注册后方可发起；未注册时给出明确错误。
- [ ] 事件名与日志随重命名更新；相关单测通过。
