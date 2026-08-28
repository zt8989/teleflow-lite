# 04 — Audio Routing / Media Bridge (lossless two-way)

**What to build:** The core tracer bullet. The Audio Routing / Media Bridge wires the established RTP session to the user-selected devices: decoded downstream audio is written directly to the chosen playback device and upstream audio is captured from the chosen capture device into RTP. No recording, no mixing, no DSP. Combines the Audio Device Manager (02) with the SIP Core Service (03).

**Blocked by:** 02 — Audio Device Manager & device selection; 03 — SIP Core Service.

**Status:** ready-for-agent

- [ ] With a call active and a virtual sound card selected as playback, downstream telephone audio is observable on that device.
- [ ] Upstream audio captured from the selected capture device reaches the telephone.
- [ ] No WAV file or any other recording artifact is ever produced anywhere on disk (red-line assertion).
- [ ] No DSP stage (denoise / gain / mix / transform) is applied to the audio path.
- [ ] Switching the selected device mid-call either re-routes live or triggers a clean SIP-service restart that re-establishes the call.
