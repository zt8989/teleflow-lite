# 07 — 呼入接通与挂断后状态复位

**What to build:** 修复软电话作为被叫时的两个回归：(1) 其他 SIP 向本机分机（1002）发起 INVITE 后无法接通；(2) 接通后挂断，界面仍卡在「通话中」。两者都出在 pjsua2 后端对呼叫对象的生命周期管理上。

**Blocked by:** 04 — SipCoreService 作为软电话（自动应答 INVITE 的框架已具备，本次补的是后端回调与服务的对接）。

**Status:** resolved

## 根因

1. **呼入不通**：`Account.onIncomingCall` 里用 `Call(self)` 创建呼叫对象，未传入真实 call id。查阅 pjsua2 源码 `call.cpp` 的 `Call::Call(Account&, int call_id)`：仅当 `call_id != PJSUA_INVALID_ID` 时才会把该 Python `Call` 对象绑定到实际 SIP 呼叫（设置 user data 并赋予可用 id）。默认 `-1` 下对象游离，`SipCoreService` 随后驱动的 `backend.answer(call_id)` 实际作用在 `id=-1` 的无效对象上，对端永远收不到 200 OK → 无法接通。
2. **挂断后仍显示通话中**：`Call.onCallState` 在 `DISCONNECTED` 时只从 `backend._calls` 移除条目，却**没有**向后端 `handler` 发出 `"bye"` 事件。`SipCoreService._dispatch` 只在收到 `"bye"` / `"cancel"` 时才把 `CallState` 复位到 `IDLE` 并 emit `EVENT_CALL_ENDED`，于是服务一直停在 `CONNECTED`，UI 卡在「通话中」。（此回调此前因第 1 个根因：对象未绑定，根本不会派发到正确的 `Call` 实例；第 1 个根因修复后，本问题才暴露。）

## 修复

- `onIncomingCall`：`call = Call(self, call_id=int(prm.callId))`，让对象真正绑定到呼入呼叫。
- `onCallState`：`DISCONNECTED` 时（非 report 呼叫，report 走自身的 `report_eof` 生命周期）向 handler 发 `"bye"`，`{"call_id": ...}`。

涉及文件：`src/teleflow/pjsua2_backend.py`（`onIncomingCall`、`onCallState`）；状态机消费方为 `src/teleflow/sip.py` 的 `SipCoreService._dispatch`。

## 验证

- 新增回归测试 `tests/test_pjsua2_incoming_call.py`：用忠实的 fake pjsua2（按 `call.cpp` 的绑定契约建模）断言 (a) `onIncomingCall` 产生的 `Call` 携带真实 call id；(b) `onCallState(DISCONNECTED)` 会发出 `"bye"` 且从 `_calls` 移除。已验证修复前该测试 RED、修复后 GREEN。
- 全量测试 88 passed / 19 skipped（19 项为依赖原生 pjsua2 的用例，本沙箱无原生库）。

## Comments

- 同一文件还存在一组未提交的「出站呼叫 SDP 文本媒体（T.140）415 拒绝」修复（`_new_call_op` 等），与本 ticket 的呼入问题无关，未纳入本次提交，留待单独提交。
- 原生 pjsua2 不在本沙箱，验证靠 fake-pjsua2 接线测试；建议在实机上用另一 SIP 拨打 1002 并挂断做一次端到端确认。
