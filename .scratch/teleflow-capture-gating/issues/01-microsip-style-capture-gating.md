# 01 — 采集端点按"所选输入设备"开关：对齐 MicroSIP，消除无谓的麦克风提示

**What to build:** 让普通呼入是否打开麦克风（采集端点），**纯粹由当前选择的输入设备决定**，表现与 MicroSIP 一致：选了输入设备才双向桥接；未选输入设备（capture = 空 / `"none"`）则只下行、不打开任何采集端点，从而不触发系统"麦克风"隐私提示。复用现有 report 通话的 `PJSUA_SND_NULL_DEV` 规避写法（`pjsua2_backend.py:434`），不新增独立总开关。

**Why:** 用户场景为固定座机 → ATA → FreeSWITCH → TeleFlow → 经 VB-Cable 播放端输出 → 系统把 VB-Cable 录音端当"麦克风"喂给三方 APP。TeleFlow 在此链路里只是 VB-Cable 的写入方，不需要采集。但当前普通呼入在 `pjsua2_backend.py:155` 无条件 `getCaptureDevMedia().startTransmit(call_audio)`，且 `start()` 时 `_apply_route()` 立刻打开采集设备（`pjsua2_backend.py:338-340`）。只要选了任意采集设备（含 VB-Cable 虚拟输入），系统就对**所有**音频输入端点弹麦克风提示——因为系统不区分真麦克风与虚拟设备。MicroSIP 在没配输入设备时不打开麦克风，故不弹；TeleFlow 应据此对齐。

**行为模型（已确认）：**
- 选了输入设备 → 双向桥接（现有行为）。
- 未选输入设备（capture 空）→ 只下行，不打开采集端点；显式 `PJSUA_SND_NULL_DEV`。
- 每一路（播放 / 采集）是否打开，由该路自己的设备选择派生。

**涉及模块：**
- `audio.py`：`AudioDeviceManager.set_selection` 当前对 `""/-1/None` 一律 `raise ValueError`（`audio.py:219-221`）；需放宽——**capture 允许为空**（playback 仍必选，除非也显式清空）。`apply_preset("production")`（`audio.py:228-242`）改为 playback=VB-Cable、capture=空；`apply_preset("debug")` 保持 playback=物理、capture=物理（双向）。
- `pjsua2_backend.py`：
  - `onCallMediaState` 普通呼入分支（`pjsua2_backend.py:147-155`）：仅当配置了 capture 设备时才执行 `dev_mgr.getCaptureDevMedia().startTransmit(call_audio)`；否则跳过该上行桥接。
  - `_apply_route()`（`pjsua2_backend.py:329-340`）：capture 为空时已 `if cap not in ("","-1",None): apply_capture` 跳过；需补充——capture 为空时显式 `setCaptureDev(PJSUA_SND_NULL_DEV)`（参考 `_place_report_call` 的 `pjsua2_backend.py:434`），确保不会回落到默认麦克风而弹出提示。
  - 注意：`getCaptureDevMedia()` 在**未**显式 `setCaptureDev` 时会用默认采集设备，所以"跳过 startTransmit"还不够，必须显式置 NULL dev。
- `ConfigStore` / `Settings`：允许 `capture_device_id` 为空串（JSON 往返正确）。
- UI（`app.py`）：capture 下拉框增加"不采集 / 无"选项，允许清空采集设备；选择后经既有 `_apply_route` 即时重路由。

**Blocked by:** None — 规避机制（`PJSUA_SND_NULL_DEV`）已随 report 通话交付，可直接复用。

**Status:** done

- [ ] `AudioDeviceManager.set_selection` 放宽：capture 允许 `""/-1/None`（不抛错）；playback 仍必须非空（或同样允许单侧清空，按 MicroSIP 对称处理）。
- [ ] `Settings.capture_device_id` 默认仍为某个物理设备（保持现有默认双向行为不回退）；空串代表"不采集/单向"。
- [ ] `onCallMediaState` 普通呼入分支：仅当 `settings.capture_device_id` 非空且非 `"-1"` 时才执行上行 `getCaptureDevMedia().startTransmit(call_audio)`；否则跳过。
- [ ] `_apply_route()`：capture 为空时显式 `setCaptureDev(PJSUA_SND_NULL_DEV)`（复用 report 路径写法），杜绝回落默认麦克风。
- [ ] `apply_preset("production")` 改为 playback=VB-Cable、capture=空；`apply_preset("debug")` 保持 playback=物理、capture=物理。
- [ ] UI capture 下拉增加"不采集 / 无"项；清空后即时重路由且不弹麦克风提示。
- [ ] 红线断言：单向模式下不录制、不做 DSP；仅跳过上行桥接，下行（VB-Cable 播放）照常。
- [ ] 单元覆盖：capture 为空 → 普通呼入不调用上行 `startTransmit` 且 `PJSUA_SND_NULL_DEV` 被设置（断言 `getCaptureDevMedia().startTransmit` 未被调用 / NULL dev 被置）；capture 有值 → 双向照常；生产模式预设结果 = playback=VB-Cable、capture 空。可在无 pjsua2 / 无硬件下用 `FakeSipBackend` 风格的断言验证桥接分支。

## Decisions (confirmed 2026-08-29)

1. **不引入独立总开关**：是否打开麦克风完全由"当前选择的输入设备"派生（对齐 MicroSIP），而非新增 `capture_enabled` 布尔。capture 为空即单向。
2. **复用 `PJSUA_SND_NULL_DEV`**：单向时显式置 NULL dev，避免 `getCaptureDevMedia()` 回落默认麦克风而弹提示（这是 report 通话已在用的规避写法）。
3. **生产模式语义修正**：从"播放=VB-Cable 且 采集=VB-Cable"改为"播放=VB-Cable、采集=空"，匹配用户"VB-Cable 仅作播放端、喂给三方 APP 当麦克风"的真实链路。

## Notes

- 系统麦克风提示只针对**输入**端点；VB-Cable 作**播放端**（输出）不会触发。所以单向模式下 TeleFlow 不应打开任何输入端点，提示自然消失。
- 三方 APP 自身会打开 VB-Cable 录音端作其麦克风，那一侧的提示属于三方 APP、理应保留，不是本 feature 要消除的。
- 当前 `set_selection` 把空 capture 判为非法（`audio.py:219-221`），本 feature 必须放开这一约束，否则"不采集"状态无法持久化。
- report / IVR 通话已独立走"不接麦克风"路径，本 feature 只改**普通呼入**与 `_apply_route` 的采集分支，不影响 report/IVR。

## Resolution (2026-08-29)

实现完成，全量测试通过（pytest 退出码 0，含真实 pjsua2 后端用例），mypy 无报错。改动：

- `src/teleflow/audio.py`
  - `AudioDeviceManager.set_selection`：播放端仍必选；采集端空值（`""/-1/None`）不再抛错，归一化为 `""`（单向）。
  - `apply_preset("production")`：改为 playback=VB-Cable、capture=空；`apply_preset("debug")` 保持双向物理设备。
- `src/teleflow/media.py`：新增纯函数 `capture_device_selected(device_id)`，作为"是否打开麦克风"的唯一判定（空 / `-1` / `None` → 单向），可无 pjsua2 单测。
- `src/teleflow/pjsua2_backend.py`
  - `_apply_route`：capture 为空时显式 `setCaptureDev(PJSUA_SND_NULL_DEV)`，杜绝回落默认麦克风。
  - `onCallMediaState` 普通呼入分支：仅当 `capture_device_selected(cap)` 才执行上行 `getCaptureDevMedia().startTransmit`；并先 `apply_capture(cap)` 重选，避免 report 通话置的 NULL sink 泄漏到普通通话。
- `src/teleflow/app.py`：`_populate_devices` 在采集下拉首部加"不采集（单向）"项（userData 为空串），对应生产模式默认选中。
- `tests/test_audio.py`：更新生产预设断言为 `("vb-cable","")`；把"空 capture 被拒"拆成"播放端必选仍拒"与"采集端空=单向且持久化"两组。
- `tests/test_media.py`：新增 `capture_device_selected` 判定用例（字符串 / int `-1` 均覆盖）。

用户场景（座机→ATA→FreeSWITCH→TeleFlow→VB-Cable→三方 APP 当麦克风）现已成立：TeleFlow 只开 VB-Cable 播放端（输出，不触发麦克风提示），采集端留空即单向；三方 APP 自身的麦克风提示不在本 feature 范围内。
