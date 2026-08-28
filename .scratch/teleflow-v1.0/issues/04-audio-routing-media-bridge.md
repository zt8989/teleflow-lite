# 04 — Audio Routing / Media Bridge (lossless two-way)

**What to build:** The core tracer bullet. The Audio Routing / Media Bridge wires the established RTP session to the user-selected devices: decoded downstream audio is written directly to the chosen playback device and upstream audio is captured from the chosen capture device into RTP. No recording, no mixing, no DSP. Combines the Audio Device Manager (02) with the SIP Core Service (03).

**Blocked by:** 02 — Audio Device Manager & device selection; 03 — SIP Core Service.

**Status:** resolved (code complete + unit-tested; live audio path needs hardware to verify)

## What landed
- `src/teleflow/media.py` — pure routing policy, no native deps:
  - `AudioRoute` dataclass (`playback_device_id`, `capture_device_id`).
  - `AudioDeviceController` Protocol exposing **only** `set_playback_device` /
    `set_capture_device` — deliberately no recorder / DSP / mixer surface, which
    encodes the red line in the type system.
  - `MediaBridge.apply(route)` drives both setters. Two-way but never records.
- `src/teleflow/pjsua2_backend.py` — real backend (guarded, `# pragma: no cover`
  for the native-only parts):
  - `Pjsua2Backend` wraps the **process-singleton** `pj.Endpoint`; `start`/
    `answer`/`reroute`/`stop` lifecycle.
  - On `onCallMediaState`, when the audio media is `ACTIVE`, bridges the call to
    the selected devices through the pjsua2 conference bridge (downstream
    `playback.startTransmit(call)`; upstream `call.startTransmit(capture)`).
  - `onIncomingCall` stores the call and notifies the service **without**
    answering — the SipCoreService drives `answer()`.
  - `_apply_route` early-returns on empty/`"-1"` device ids so the default
    (no device selected) config cannot crash on `int("")`.
- `src/teleflow/app.py` — `_default_sip_backend()` prefers `Pjsua2Backend`
  (falls back to `FakeSipBackend` on `RuntimeError`), and re-routes a live call
  when the user switches the selected device (`EVENT_DEVICE_SELECTED` →
  `backend.reroute()`).
- `src/teleflow/config.py` — fixed `ConfigStore.__init__` to always store a
  `Path` (was `path or DEFAULT`, which could store a raw `str` and break
  `load()`/`save()`).

## Verification
- `tests/test_media.py` — 3 tests: downstream+upstream routing, **red-line
  assertion that only the two device setters are ever called** (no recording /
  no DSP), and live re-route.
- `tests/test_pjsua2_backend.py` — gated native test (skipped unless the
  pjsua2 extension is built): one backend per process (Endpoint is a singleton),
  starts/stops a real UDP transport, and tolerates "no device selected".
- `tests/test_config.py` — regression test for the `Path` fix.
- `mypy src/teleflow`: clean. `pytest`: 33/33 green.

## Acceptance criteria — code-level vs. hardware-dependent
- [x] No WAV/recording artifact is ever produced (enforced in `audio.py` +
      `media.py` types + `test_apply_performs_no_recording_or_dsp`).
- [x] No DSP stage on the audio path (no denoise/gain/mix/transform anywhere).
- [x] Switching the selected device mid-call re-routes live via `reroute()`.
- [ ] With a call active and a virtual sound card selected as playback,
      downstream telephone audio is observable on that device — **requires a
      real ATA gateway + a virtual sound card (e.g. BlackHole/Loopback) to
      verify end-to-end; not reachable in CI/headless.**
- [ ] Upstream audio captured from the selected capture device reaches the
      telephone — **same hardware dependency.**

## Follow-ups
- Ticket 07 (packaging) must bundle the native pjsua2 extension + its dynamic
  deps (`-lssl -lcrypto -lgnutls -lSDL2`, CoreAudio frameworks).
- Ticket 08 (stability) should cover pjsua2 callback exceptions and
  lib-recreate on fatal transport error (Endpoint is a singleton, so recovery
  means re-init within the same process, not a new Endpoint).
