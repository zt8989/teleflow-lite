# 04: 原生后端 DTMF 回调与单向非挂断播放

**What to build:** pjsua2 真实后端能接收来电者 DTMF 按键并上报服务；IVR 把文件播放进通话后**不再自动挂断**（仅释放播放器）；呼入 IVR call 不桥接麦克风；`FakeSipBackend` 提供 `receive_dtmf` 与播放完成信号，使 IVR 链路可在无硬件、无 pjsua2 下端到端测试。

**Blocked by:** None (can start immediately)

**Status:** done

- [ ] `Call` 子类实现 `onDtmfDigit(digit)`，转发 `backend._handler("dtmf", {"call_id": ..., "digit": ...})`。
- [ ] `play_file_to_call` 增加 `hangup_on_eof: bool = False` 参数；`False` 时 EOF 仅释放播放器并 emit `"playback_done"`，不挂断（与 report call 的 EOF 自动挂断分离）。
- [ ] `onCallMediaState` 支持 `_is_ivr` 跳过麦克风桥接（与 `_is_report` 同逻辑）；新增 `Pjsua2Backend.mark_ivr(call_id)` 设置该标记。
- [ ] `FakeSipBackend.play_file_to_call` 在 `hangup_on_eof=False` 时 `fire("playback_done", call_id=call_id)`；新增 `receive_dtmf(call_id, digit)` 测试钩子。
- [ ] `SipBackend` 协议更新 `play_file_to_call` 签名（新增 `hangup_on_eof` 默认 `False`）。
