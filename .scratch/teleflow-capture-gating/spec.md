# Spec: teleflow-capture-gating

## 背景

当前普通呼入在 `pjsua2_backend.py:150-155` 无条件把麦克风桥接到电话
（`getCaptureDevMedia().startTransmit(call_audio)`），且后端 `start()` 时
`_apply_route()`（`pjsua2_backend.py:338-340`）立刻打开采集设备。只要选了**任意**
采集设备（包括 VB-Cable 的虚拟输入），系统就会弹"麦克风"隐私提示——因为系统对
所有音频**输入**端点一视同仁，不区分真麦克风与虚拟设备。

用户场景（已确认 2026-08-29）：固定座机 → ATA → FreeSWITCH → TeleFlow → 经
VB-Cable 播放端输出 → 系统把 VB-Cable 录音端当作"麦克风"喂给三方 APP 作语音输入。
TeleFlow 在此链路里只是 VB-Cable 的**写入方**（往播放端灌座机语音），根本不需要
采集。当前行为却强行打开采集端点，既无用又触发提示。

MicroSIP 的表现：是否打开麦克风完全由"当前是否选择了**输入设备**"决定——没选输入
设备就不打开，自然不弹。本 feature 要对齐这一行为。

## 行为模型（核心，已与用户确认）

**采集端点是否打开，纯粹由"当前选择的输入设备"决定**，不引入额外的硬编码开关：

- 选了输入（capture）设备 → 普通呼入双向桥接（现有行为）。
- 未选输入设备（capture = 空 / `"none"`）→ 普通呼入只下行（播放），**不打开任何**
  采集端点；显式 `setCaptureDev(PJSUA_SND_NULL_DEV)`（复用 report 路径写法），
  不触发系统麦克风提示。
- 对称地，播放端点同理（选了才开）。本场景 playback=VB-Cable 必选，capture 留空。

即：每一路（播放 / 采集）是否打开，由该路自己的设备选择派生——与 MicroSIP 一致。

## 涉及改动

- `audio.py`：`AudioDeviceManager.set_selection` 放宽对 capture 的空值校验；
  `apply_preset("production")` 改为 playback=VB-Cable、capture=空（而非当前强制
  capture=VB-Cable，`audio.py:235-237`）。
- `pjsua2_backend.py`：`onCallMediaState` 普通分支按 capture 是否配置决定上行桥接；
  `_apply_route` 在 capture 为空时显式 `PJSUA_SND_NULL_DEV`。
- UI：capture 下拉框增加"不采集 / 无"选项，允许清空采集设备。
- `ConfigStore` / `Settings`：允许 `capture_device_id` 为空串。

## 红线

单向（不采集）模式下仍不录制、不做 DSP；仅跳过上行桥接，下行（VB-Cable 播放）照常。
