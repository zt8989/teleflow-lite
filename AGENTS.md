# AGENTS.md

## Repository

TeleFlow — 座机声音流转助手: a **local-only SIP UA** desktop app (PyQt6) that auto-answers
inbound calls from an ATA gateway and bridges call audio losslessly to a user-selected
sound card (playback device for downlink, capture device for uplink). No native SIP/audio
lib or display is required to run the logic or tests — the transport is behind a swappable
backend seam (see Architecture).

Major directories:
- `src/teleflow/` — all application code.
- `tests/` — pytest suite (runs against the scripted `FakeSipBackend`, no network/native lib).
- `.scratch/<feature-slug>/` — local markdown issue tracker + feature specs.
- `docs/` — `agents/` (tracker/triage/domain conventions), `build-pjsua2.md`, `packaging.md`.
- `prototypes/teleflow-ui-prototype.html` — interactive UI mock (tray → Settings → hooks).

## Commands

> **Environment**: this is a local engineering project; all dependencies live in the
> workspace virtualenv at `.venv/` — they are **not** installed globally and the global
> `python`/`pip` must **not** be used to run or install for this repo. Always invoke the
> venv interpreter, e.g. on Windows `.venv\Scripts\python.exe -m pytest` (or activate the
> venv first). `pip install -e ".[dev]"` should target the venv too.

```bash
.venv\Scripts\python.exe -m pip install -e ".[dev]"   # runtime (PyQt6, edge-tts) + dev (pytest, mypy)
.venv\Scripts\python.exe -m pytest                     # full suite; pythonpath=["src"], addopts=-q
.venv\Scripts\python.exe -m mypy src/teleflow           # type-check; targets Python 3.10, ignore_missing_imports
```
GUI tests / app launch need `QT_QPA_PLATFORM=offscreen` in headless environments.

## Architecture

- **Backend seam**: `SipBackend` (Protocol) in `sip.py`. Real transport = `Pjsua2Backend`
  (`pjsua2_backend.py`, needs the separately-built pjsua2 native lib). Scripted twin =
  `FakeSipBackend` (in `sip.py`) for tests. `SipCoreService` owns all call *state* and
  translates raw backend events (`invite`/`bye`/`dtmf`/`playback_done`/`report_*`) into
  domain events via `_dispatch`/`_fire`.
- **Domain events** are string constants in `sip.py` (`EVENT_CALL_CONNECTED`,
  `EVENT_CALL_ENDED`, `EVENT_IVR_DIGIT`, `EVENT_REPORT_*`, …). `_emit` calls handlers as
  `callback(**data)`, so **every subscriber must accept the event's kwargs** (give unused
  ones defaults, e.g. `def _on_ended(call_id, last_digit="")`).
- **IVR** vs **Phone-Report** are distinct inbound flows. Report calls auto-hang-up on EOF
  and emit `EVENT_REPORT_*` but **not** `CALL_ENDED`; IVR calls emit `CALL_ENDED` (so the
  on-hook hook fires). Both share the `_report_players` playback dict and the `_is_ivr` /
  `_is_report` mic-suppression flags in the backend.
- **TTS**: `TtsBackend` Protocol; `EdgeTtsBackend` (edge-tts + **ffmpeg** to make an 8 kHz
  mono WAV pjsua2 can play), `FakeTtsBackend` (tests), `CachingTtsBackend` (cache keyed by
  `sha256(clean_markdown(text)+"\0"+voice)[:16]`, reused until text/voice changes).
  `clean_markdown` strips Markdown before synthesis.
- **Hooks**: `HookRunner` Protocol + `SubprocessHookRunner` (non-blocking daemon thread,
  `shell=True`, swallows empty/errors). `attach_hooks` (in `hooks.py`) wires off-hook →
  `CALL_CONNECTED`, on-hook → `CALL_ENDED` (context now includes `{last_digit}`), and
  per-digit IVR → `EVENT_IVR_DIGIT` (context `{digit}`; **empty per-digit commands are
  skipped entirely**). Commands read live config at fire time — a settings change takes
  effect on the next call, no restart.

## Conventions & gotchas

- **Red line — never cross**: do **not** record calls, write a call WAV, or apply any DSP
  (denoise/gain/mix) on the live audio path. `media.py` / `pjsua2_backend.py` keep the
  bridge free of recorders/transforms *by design*. The only sanctioned WAV I/O is TTS-
  synthesized playback (tts.py) and reading DTMF signaling — both are deliberate exceptions.
- **Logging prefixes**: use `[HOOK]`, `[IVR]`, `[REPORT]` so lines are greppable in the
  dashboard/log view. Service logs flow through `service._log_line` (injected sink).
- **Settings persistence**: `ConfigStore` rewrites the **whole** `config.json` on save
  (under the user config dir, `~/.config/teleflow/`). Manual edits to that file get clobbered
  by the next in-app save — change settings via the Settings dialog, not by hand-editing.
- **Per-digit IVR config** is two dicts keyed by `"1".."9","0"`: `ivr_digit_text` and
  `ivr_digit_hook`; empty text = skip that prompt, empty hook = no command for that key.
- **Worktrees**: feature work goes in a git worktree with its `.scratch/<slug>` brought
  along, keeping `master` clean (per README / issue-tracker convention).
- **Security**: hook commands run via `shell=True` from user config; `{call_id}` is taken
  verbatim from the gateway's INVITE (untrusted input) — treat as a potential shell-injection
  source on untrusted networks.
