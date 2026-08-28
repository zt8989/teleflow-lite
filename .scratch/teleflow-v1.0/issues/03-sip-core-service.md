# 03 — SIP Core Service (register, auto-answer, media events)

**What to build:** The SIP Core Service runs a local UA bound to `0.0.0.0:<port>` (from config). It accepts the ATA gateway's `REGISTER` with `200 OK`, stores the gateway Contact, auto-answers inbound `INVITE`, and emits domain events (`GatewayRegistered`, `CallIncoming`, `CallConnected`, `CallEnded`, `MediaError`) that the status panel reflects. Call state resets cleanly on `BYE` / `CANCEL` / abnormal disconnect.

**Blocked by:** 01 — App shell & Config Store.

**Status:** ready-for-agent

- [ ] UA binds to the configured port and reports SIP service running.
- [ ] A scripted ATA peer registering receives `200 OK` and its Contact is stored.
- [ ] A scripted `INVITE` is auto-answered and `CallConnected` is emitted.
- [ ] `BYE` / `CANCEL` / abnormal disconnect emits `CallEnded` and resets state to idle.
- [ ] Status panel shows SIP state, gateway registration, and call state (空闲 / 呼入 / 通话中 / 挂断).
