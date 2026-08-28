# TeleFlow — Spec (V1.0)

- **Status:** `ready-for-agent`
- **Labels:** `ready-for-agent`
- **Source:** TeleFlow 产品需求文档（PRD V1.0 最终定稿）
- **Feature slug:** `teleflow-v1.0`

---

## Problem Statement

The user owns a physical **Telephone 座机** wired through an **ATA 电话网关** that speaks SIP. They want the full two-way call audio to flow into their computer's audio stack — specifically into a **虚拟声卡** (VB-Cable on Windows, BlackHole on macOS) — so that external recording / ASR / AI software can read it from that virtual endpoint.

Today the workable options are too heavy or too limiting: running FreeSwitch or Kamailio means standing up a SIP server and middleware; off-the-shelf softphones either hide the audio-device choice or don't cleanly hand audio to a virtual sound card; and nothing gives a single, local, always-on app that simply bridges the ATA gateway's SIP calls to freely chosen audio devices.

The user needs a lightweight, local-only desktop app that acts as a SIP UA, lets the ATA gateway register to it, auto-answers calls, and routes decoded RTP audio straight to a user-selected playback device while capturing from a user-selected capture device — with **no internal recording and no audio processing of any kind**.

## Solution

**TeleFlow** is a cross-platform (Windows / macOS) standalone desktop app built on **PyQt6 + PJSUA2** that:

- Runs a **local SIP UA** bound to `0.0.0.0:<port>` (default `5060`, configurable). It accepts the ATA gateway's `REGISTER`, replies `200 OK`, and stores the gateway **Contact** so TeleFlow can also place calls to the telephone.
- Listens for inbound `INVITE`, **auto-answers**, and establishes the **RTP media stream** automatically; on hang-up or abnormal disconnect it resets call state cleanly.
- **Enumerates every system audio device** at startup (physical sound cards, headsets, microphones, and virtual sound cards) and exposes two **independent** dropdowns — **Speaker** (playback / downstream) and **Microphone** (capture / upstream) — exactly matching MicroSIP's device-selection capability.
- Routes decoded downstream RTP audio **directly** into the selected playback device and captures upstream audio from the selected capture device. **No recording, no mixing, no DSP, no codec post-processing** — the audio is handed whole to the system audio stack.
- Supports a **调试模式** (headset speaker + headset mic → behaves like a normal softphone) and a **生产模式** (virtual-sound-card input + virtual-sound-card output → the telephone audio flows losslessly to external apps). Mode is purely a device-selection preset, not a separate code path.
- Lives in the **system tray**, starts minimized, runs 7×24 at low CPU/RAM, and never disables audio or selects a null device (`audioDevId = -1` is forbidden).
- Persists settings (SIP port, device choices, autostart, start-minimized, log level) and writes a rolling **log** of SIP signaling, media state, and device binding to both a file and a live UI window.

## User Stories

1. As a user, I want TeleFlow to auto-enumerate all audio devices on launch so that I can choose where audio goes without manual configuration.
2. As a user, I want to independently select a playback device and a capture device so that I can route downstream and upstream audio to different endpoints (e.g. virtual sound card out, mic in).
3. As a user, I want virtual sound cards (VB-Cable on Windows, BlackHole on macOS) to appear as ordinary selectable devices so that external apps can consume the telephone audio.
4. As a user, I want a "refresh devices" action so that newly plugged-in or removed devices show up without restarting the app.
5. As a user, I want to configure the SIP listen port (default 5060) so that it fits my LAN and avoids conflicts.
6. As a user, I want TeleFlow to accept the ATA gateway's `REGISTER` and reply `200 OK` so that the gateway registers to my machine.
7. As a user, I want TeleFlow to remember the gateway's Contact address so that it can later place a call to the telephone.
8. As a user, I want inbound `INVITE` to be auto-answered so that calls connect with no manual click.
9. As a user, I want the RTP media stream to be established automatically so that bidirectional audio flows as soon as the call connects.
10. As a user, I want decoded downstream audio to be written directly to my selected playback device (lossless, unprocessed) so that an external app reading that device gets the full call.
11. As a user, I want my voice captured from my selected capture device to be sent back to the telephone so that I can talk during the call.
12. As a user, I want a 调试模式 (headset speaker + headset mic) so that I can use TeleFlow as an ordinary softphone for testing.
13. As a user, I want a 生产模式 (virtual-sound-card in/out) so that the telephone audio flows losslessly to my recording / ASR / AI tooling.
14. As a user, I want call state to auto-reset on hang-up, `BYE`, `CANCEL`, or abnormal disconnect so that the next call starts clean.
15. As a user, I want a status panel showing SIP service state, selected devices, gateway registration, and live call state (空闲 / 呼入 / 通话中 / 挂断) so that I have at-a-glance visibility.
16. As a user, I want a real-time scrolling log window so that I can watch SIP signaling and media events as they happen.
17. As a user, I want a settings page for SIP port, device selection, refresh, autostart, start-minimized, and log level so that I can configure the app to my environment.
18. As a user, I want the app to minimize to the system tray so that it stays out of my way while running.
19. As a user, I want a tray right-click menu (start service, stop service, show window, quit) so that I can control TeleFlow without opening the main window.
20. As a user, I want low-power background residency so that it can stay running 7×24.
21. As a user, I want logs persisted to a local file so that I can audit past sessions.
22. As a user, I want a device change to take effect (live re-route when possible, otherwise on SIP service restart) so that I am never stuck on a wrong device.
23. As a Windows user, I want VB-Cable recognized without microphone-permission popups so that operation is uninterrupted.
24. As a macOS user, I want BlackHole recognized and audio permission requested correctly (no crash, no black screen) so that it is stable on Intel and Apple Silicon.
25. As a user, I want a single-file Windows EXE and a macOS DMG so that deployment is trivial.
26. As a user, I want stable 7×24 operation with no memory leak, no audio drop-outs, and no drift across repeated calls so that it is reliable for long-term use.
27. As a user, I want automatic recovery on network drop and on audio-device hotplug so that transient faults don't require manual intervention.
28. As a user, I want TeleFlow to never disable audio, never select a null device, and never set `audioDevId = -1` so that audio is always wired up.
29. As a user, I want no dependency on FreeSwitch, Kamailio, or any SIP server so that the app is fully self-contained.
30. As a user, I want no external SIP registration or account login so that the app stays local-only.
31. As a user, I want no conferencing, transfer, or IVR so that scope stays minimal and focused.
32. As a user, I want TeleFlow to never record audio or write WAV files or run any DSP so that it remains a pure audio router.

## Implementation Decisions

### Modules to build (logical, no file paths)

- **SIP Core Service (Local UA)** — binds `0.0.0.0:<port>`, handles `REGISTER` (`200 OK`, store Contact), `INVITE` (auto-answer), `BYE`/`CANCEL`, SDP media negotiation, and auto-resets call state on hang-up / abnormal disconnect. Built on PJSUA2. Port is configurable.
- **Audio Device Manager** — enumerates PortAudio devices at startup and on refresh; exposes independent **Speaker** (playback) and **Microphone** (capture) selection; guarantees no null device and never `audioDevId = -1`.
- **Audio Routing / Media Bridge** — once RTP is up, writes decoded downstream audio to the selected playback device and captures upstream audio from the selected capture device into RTP. **No recording, no mixing, no DSP.**
- **Settings / Config Store** — persists SIP port, playback device id, capture device id, autostart, start-minimized, and log level; loads them on launch.
- **UI Layer (PyQt6)** — status panel, settings page, live log view.
- **System Tray Integration** — minimize-to-tray, tray menu (start/stop service, show window, quit), background residency.
- **Logging Subsystem** — emits SIP, media, and device-binding events to both a file and the UI.

### Interfaces / contracts

- **Audio Device Manager** exposes a device catalog (`id`, `name`, `kind: physical|virtual`, `direction: playback|capture`) and a selection setter. A selection change triggers either a live media re-route (when a call is active) or a SIP-service restart (when media cannot be hot-swapped).
- **SIP Core Service** raises domain events consumed by the UI status panel and the Logging Subsystem: `GatewayRegistered(contact)`, `CallIncoming`, `CallConnected`, `CallEnded`, `MediaError`.
- **Config Store** exposes a load/save contract over a single settings record.

### Architectural decisions

- **Local-only UA, single process, no SIP server, no external registration.** This is the defining constraint and removes all middleware.
- **PJSUA2 is the SIP core.** It is the only Python-capable SIP stack with mature audio-device selection and stable ATA registration / call-event callbacks; libbaresip was rejected for lacking stable Python bindings.
- **PortAudio (PJSUA2 native) is the audio backend**, so virtual sound cards surface as ordinary devices and the MicroSIP-style independent device choice is achievable.
- **Decoupling principle (the core architectural seam):** TeleFlow never records or processes audio. External recording / ASR / AI software read the virtual sound card directly. TeleFlow's only contract with the outside world is "audio lands on the OS endpoint you selected."
- **Mode is a preset, not a branch.** 调试模式 and 生产模式 are just two device-selection defaults; there is no separate code path for either.

### Config record shape (schema)

A single settings record: `sip_port`, `playback_device_id`, `capture_device_id`, `autostart`, `start_minimized`, `log_level`. No migration needed (greenfield).

### Cross-platform notes

- Windows: recognize VB-Cable; avoid mic-permission popups; package as a single EXE via PyInstaller.
- macOS: recognize BlackHole; request audio permission correctly on Intel and Apple Silicon; package as DMG via PyInstaller.

## Testing Decisions

- **Test external behavior, not implementation.** Assert observable outcomes through each module's public interface; do not assert on internal call sequences or private state.
- **Proposed seams (highest available):** drive the **SIP Core Service** and the **Audio Device Manager** through their public interfaces. The SIP UA and the audio backend are behind adapters so they can be substituted with fakes in CI:
  - A **scripted SIP peer** acts as the ATA gateway (register → `200 OK` observed; `INVITE` → auto-answer observed).
  - A **fake audio device backend** exposes virtual devices so enumeration, independent selection, and the "no null device" rule can be asserted without real hardware.
  - This keeps the seam count to effectively one integration boundary per module.
- **Modules to test:**
  - *SIP Core Service* — `REGISTER` yields `200 OK` and stores Contact; inbound `INVITE` is auto-answered; `BYE`/`CANCEL`/abnormal disconnect cleanly resets state.
  - *Audio Device Manager* — enumeration includes virtual sound cards; playback and capture are independently selectable; selecting a null device / `audioDevId = -1` is rejected.
  - *Config Store* — settings round-trip (save then load yields identical record).
  - *Logging Subsystem* — emits the expected events on register / call / device-bind.
- **Red-line assertion:** across the full integration path (gateway registers, call connects, audio routed), assert that **no recording file (WAV) is ever produced** and no DSP stage runs — encode the PRD's 红线规则 directly as a test.
- **Prior art:** greenfield; tests are new. Follow the port/adapter pattern above so the SIP UA and audio backend are exercisable headlessly. Cross-platform virtual-driver behavior (VB-Cable / BlackHole) is verified manually since those drivers cannot run in CI.

## Out of Scope

- No internal audio recording or WAV output.
- No audio DSP: denoise, gain, mixing, voice change.
- No FreeSwitch / Kamailio / any SIP server dependency.
- No external SIP registration or account login.
- No conferencing, call transfer, or IVR.
- No forced disabling of physical sound cards.
- No mobile or web client.

## Further Notes

- The PRD notes parts may be AI-generated; this spec is synthesized from it without changing intent.
- **Performance & stability:** idle state must be very low CPU/RAM; support 7×24 uptime; no memory leak, no audio drop-out, no drift across repeated calls; auto-recover on network drop and device hotplug.
- **Delivery:** full PyQt6 source (runs as-is), Windows single-file EXE, macOS DMG, log + config mechanism, and a deploy/usage document.
- **Suggested next step:** break this spec into tracer-bullet tickets via `/to-tickets` once the user confirms the testing seams above.
