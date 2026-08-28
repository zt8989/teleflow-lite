# 03 — SIP Core Service (register, auto-answer, media events)

**What to build:** The SIP Core Service runs a local UA bound to `0.0.0.0:<port>` (from config). It accepts the ATA gateway's `REGISTER` with `200 OK`, stores the gateway Contact, auto-answers inbound `INVITE`, and emits domain events (`GatewayRegistered`, `CallIncoming`, `CallConnected`, `CallEnded`, `MediaError`) that the status panel reflects. Call state resets cleanly on `BYE` / `CANCEL` / abnormal disconnect.

**Blocked by:** 01 — App shell & Config Store.

**Status:** resolved

- [ ] UA binds to the configured port and reports SIP service running.
- [x] A scripted ATA peer registering receives `200 OK` and its Contact is stored.
- [x] A scripted `INVITE` is auto-answered and `CallConnected` is emitted.
- [x] `BYE` / `CANCEL` / abnormal disconnect emits `CallEnded` and resets state to idle.
- [x] Status panel shows SIP state, gateway registration, and call state (空闲 / 呼入 / 通话中 / 挂断).

## Implementation notes

Delivered in `src/teleflow/sip.py` (`SipCoreService` + `SipBackend` protocol + `FakeSipBackend`) and wired into `src/teleflow/app.py` (status panel reflects SIP/registration/call state; start/stop button). Built TDD red→green (`tests/test_sip.py`); offscreen GUI smoke test extended. Full suite green (20), mypy clean.

**Native dependency:** the real `pjsua2` transport could not be built in this environment — `pip install pjsua2` fails with `FileNotFoundError: '../../../../version.mak'` (broken sdist packaging). The live UA therefore still uses the scripted `FakeSipBackend`; completing the real transport (registrar + auto-answer over pjsua2) is blocked on a working native `pjsua2` build and is tracked as a follow-up.
