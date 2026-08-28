# 02 — TTS synthesis layer

**What to build:** Given report text (and an optional voice), produce a pjsua2-playable 8 kHz mono WAV by stripping Markdown, synthesizing speech with `edge-tts` (mp3), then transcoding with an external `ffmpeg`. ffmpeg is located via the configured path or `PATH`, with a clear error if neither exists. A fake backend lets the rest of the system run headless/CI without network or ffmpeg.

**Blocked by:** 01 — report-config-schema

**Status:** resolved

- [ ] `clean_markdown` strips headings, bold/italic, inline code, links, and table pipes so TTS reads only prose (port the user's existing cleaning rules).
- [ ] The real backend synthesizes mp3 via edge-tts using the chosen voice and transcodes to 8 kHz mono `pcm_s16le` wav via ffmpeg.
- [ ] ffmpeg resolution order: use `ffmpeg_path` if set, otherwise `shutil.which("ffmpeg")`; if neither resolves, raise a clear `FfmpegNotFound` (no silent failure / no partial file).
- [ ] A `TtsBackend` protocol plus a `FakeTtsBackend` (returns a canned wav path) exist so the controller and RPC can be tested without edge-tts/ffmpeg/network.
- [ ] Synthesized wav paths are absolute / forward-slash; transient products land in the configured cache dir.
