# TeleFlow — Desk-Phone Audio Router

> 中文文档见 [README.zh-CN.md](README.zh-CN.md).

TeleFlow is a **local-only SIP user-agent (UA)** desktop app (PyQt6). It listens for
inbound calls to the current SIP account coming from a telephone gateway (ATA),
**auto-answers** them, and bridges the call audio losslessly to a user-selected
sound card: downlink is written to the playback device, uplink is taken from the
capture device. The playback and capture devices can be chosen independently (for
example, a virtual sound card in production mode, headphones in debug mode).

- Real transport: the `pjsua2` native library (see `docs/build-pjsua2.md`).
- Tests / headless environments: a scripted `FakeSipBackend` gateway runs the entire
  logic with no network or native library required.

---

## Quick start

```bash
# Dependencies are managed with uv (https://github.com/astral-sh/uv); `uv.lock` pins them.
uv sync                        # install PyQt6, edge-tts and dev tools
# pjsua2 (the native SIP transport) is a vendored wheel (docs/build-pjsua2.md) shipped as an
# OPTIONAL extra: build it for your platform, then `uv sync --extra pjsua2` installs it. Keeping
# it optional lets a plain `uv sync` succeed on every platform before the wheel exists. Windows
# is built with MSYS2/MinGW-w64 UCRT (docs/build-pjsua2.md §7).
uv run python -m teleflow.app  # launch the GUI
```

After launch the window minimizes to the system tray: the tray menu in the
bottom-right lets you **start/stop the SIP service, show the window, open Settings,
and quit**. `docs/packaging.md` covers macOS DMG packaging.

---

## Directory layout

| Path | Responsibility |
|------|----------------|
| `src/teleflow/sip.py` | SIP core service `SipCoreService` and the `SipBackend` protocol (real/fake backends interchangeable) |
| `src/teleflow/pjsua2_backend.py` | pjsua2-based real transport |
| `src/teleflow/hooks.py` | **Call-lifecycle external command hooks** (see below) |
| `src/teleflow/config.py` | Settings persistence (`Settings` + `ConfigStore`) |
| `src/teleflow/app.py` | PyQt6 app shell, dashboard, tray, settings dialog |
| `src/teleflow/audio.py` / `media.py` | Audio device enumeration and conference-bridge routing |
| `prototypes/teleflow-ui-prototype.html` | Interactive UI prototype (incl. hook config UI) |

---

## Audio routing: two-way debug vs one-way relay (production mode)

TeleFlow is only a **pure audio router**: it bridges an already-established RTP
session onto the chosen devices — downlink (phone → playback device), uplink
(capture device → phone). The playback and capture devices are **chosen
independently**, which yields two typical usages:

| Mode | Playback device | Capture device | Behavior | System mic prompt |
|------|-----------------|----------------|---------|-------------------|
| Debug mode (headphones) | Physical headphones | Physical mic | Two-way: talk on the landline normally | Yes (a real mic is used) |
| Production mode (virtual sound card) | VB-Cable / BlackHole | **No capture (empty)** | **One-way**: only the landline voice is written to the virtual card | **No** (no capture endpoint opened) |

**Production mode = MicroSIP-style**: whether the microphone is opened is decided
entirely by "whether an input device is selected". Leaving the capture device empty
means one-way; TeleFlow opens no audio input endpoint (internally it sets
`PJSUA_SND_NULL_DEV`), so the system shows no microphone-privacy prompt. Inbound IVR
no longer has a separate one-way mode: as long as a capture device is selected, the IVR
announcement still bridges the call two-way during playback, and the AI side can
interrupt at any time (see next section).

### Typical deployment: feed the landline voice to a third-party app via VB-Cable

Send the landline call audio to another program (voice assistant / transcription /
recording) as a microphone input, in real time:

```
Landline phone
   │ analog phone line
   ▼
ATA (analog telephone adapter, converts to SIP)
   │ SIP
   ▼
FreeSWITCH (IP-PBX, routing / registration)
   │ SIP INVITE
   ▼
TeleFlow (this app, auto-answers)
   │ downlink audio (playback only, no capture)
   ▼
VB-Cable (virtual sound card · playback side)
   │ the OS exposes its "record side" as a microphone
   ▼
Third-party app (selects VB-Cable's record side as its "microphone" input)
```

- In this chain TeleFlow is only the **writer** into VB-Cable: after selecting "Production
  mode (virtual sound card)", playback = VB-Cable, capture = empty.
- VB-Cable's **playback side** is written by TeleFlow (output, no mic prompt); its
  **record side** is opened by the third-party app as a microphone — that prompt belongs
  to the third-party app and should stay.
- The red line is unchanged: TeleFlow still records nothing, applies no DSP, and only
  passes the landline voice through to the virtual sound card.

---

## Inbound IVR: welcome message + per-digit-key announcement / hook

After auto-answering an inbound call, TeleFlow can enter a simple IVR: it first plays a
**welcome message**, then plays each digit key's own configured text (the menu) in order
`1-9-0`. DTMF listening is enabled right after answering — the caller can press the first
key at any time, and a key pressed **during playback also takes effect (barge-in)**,
triggering that key's hook command. Each key is configured independently with `text`
(announcement) and `hook` (command); a key with empty text is skipped in the menu, and a
key with no hook runs no command when pressed. `ivr_enabled` is the master switch (on by
default); turning it off returns TeleFlow to a pure audio router.

```
Landline inbound (INVITE)
   │
   ▼
TeleFlow auto-answers (CALL_CONNECTED)
   │  ivr_enabled = True
   ▼
Play welcome message (TTS → 8k mono wav, cacheable); the call is always bridged two-way,
so the AI side can interrupt or talk over at any time
   │
   ▼
Play each key's text in order 1-9-0 (keys with empty text are skipped); a key pressed
during playback is a "barge-in": stop the current announcement, cancel the remaining menu items
   │
   ▼
Caller presses <key> (any time)
   ├── key has a non-empty hook → run that key's command ({call_id} / {digit})   ← per key
   └── stop listening for further keys; last_digit = that key
   │
   ▼
Hang-up (CALL_ENDED)
   │
   ▼
Run on_hook_cmd ({last_digit} is substituted; empty string if no key was pressed)
```

- **Per-key independence**: `ivr_digit_text` / `ivr_digit_hook` are keyed by `"1".."9"`,
  `"0"`; a key with no text is not announced in the menu, a key with no hook runs no
  command when pressed.
- **Press any time (barge-in)**: DTMF listening is enabled right after answering, not
  after playback ends. A key pressed during the welcome/menue announcement immediately
  fires that key's hook, while stopping the current announcement and cancelling the
  remaining menu items, so the announcement tail does not cover the key action; further
  keys are still ignored (the first key wins).
- **Voice cache**: after the first TTS of the welcome message and each key's `text`, the
  wav is cached by `hash(clean_markdown(text)+voice)`; a repeat inbound with the same text
  reuses it directly.
- **Always two-way call**: during IVR playback the mic is not suppressed, so the AI side
  (via the capture device) can talk over or interrupt the caller at any time, like a
  "voice announcement + live listening" service such as 10010. The menu is driven only by
  DTMF keys and ends when the call hangs up; there is no separate "exit IVR → two-way"
  switch — as long as a capture device is selected, the inbound call is bridged two-way.
- The red line is unchanged: IVR playback only does TTS playback and DTMF reading — no
  recording, no DSP; although the call is bridged two-way, TeleFlow records no call audio,
  writes no call WAV, and applies no transformation.

---

## Phone report (outbound + one-way playback): local RPC control channel

TeleFlow can not only take inbound calls but also **dial the physical landline
outbound** and play a report. An external script (e.g. an AI assistant's Stop hook) hands
TeleFlow the **text** via a token-authenticated local HTTP request
(`POST /v1/report`); TeleFlow internally does the TTS synthesis, transcoding, outbound
call, playback, and hang-up on EOF — the external script needs no TTS or SIP of its own.
This is "calling the corresponding number back through a hook".

```
External script / hook (AI assistant task done)
   │ POST /v1/report  { "text": "…", "voice"?: "…" }
   │ Authorization: Bearer <rpc_token>
   ▼
TeleFlow local RPC service (127.0.0.1:<rpc_port>, default 8731)
   │ verify token / SIP state / landline target report_target
   ▼
TTS synthesis (edge-tts) → ffmpeg transcode to 8kHz mono wav (cacheable)
   │
   ▼
TeleFlow makeCall → landline (report_target, e.g. sip:8000@192.168.1.116)
   │ landline off-hook (EVENT_CALL_CONNECTED)
   ▼
One-way playback of wav into the call (no mic bridging)
   │ playback ends (EOF)
   ▼
Auto hang-up (EVENT_REPORT_COMPLETED)
```

- **Local and controlled**: the RPC binds only `127.0.0.1` and requires
  `Authorization: Bearer <rpc_token>`; the token is randomly generated on first launch
  and persisted, viewable/resettable in Settings. Concurrent reports return `409` (single
  report slot).
- **Text is everything**: the RPC sends only text (+ optional `voice` override); synthesis
  / transcoding / dialing all happen inside TeleFlow; an `audio_path` override is also
  supported to skip TTS and play directly.
- **Config**: `report_target` (landline target SIP URI), `report_caller_id`, `tts_voice`,
  `ffmpeg_path` (empty = auto-discover via `PATH`), `rpc_enabled` / `rpc_port` /
  `rpc_token`, plus the "Test report" button on the panel.
- The red line is unchanged: the report is **one-way playback of a synthesized file** — no
  call recording, no call WAV, no DSP.
- There are also `POST /v1/play` (play a prompt to an active inbound call) and
  `POST /v1/ivr/replay` (replay the IVR menu), plus `GET /v1/status` to probe readiness.

---

## Hook commands (off-hook / on-hook)

TeleFlow can run **local commands/scripts you configure** at key moments of the call
lifecycle. This is the simplest way to wire inbound-call events into external automation
(popup notifications, door opening, writing to a database, triggering recording, etc.).

### Two trigger points

| Name | Config field | When it fires | Event |
|------|--------------|--------------|-------|
| **Off-hook** | `off_hook_cmd` | The moment the current SIP **auto-answers** an inbound call | `CALL_CONNECTED` |
| **On-hook** | `on_hook_cmd` | When the call **ends** (landline sends `BYE`, or `CANCEL` before answer) | `CALL_ENDED` |

### Configuration

In the tray menu → **Settings**, fill in "Off-hook command" / "On-hook command" (empty =
no command). Settings are written to `~/.config/teleflow/config.json` and **take effect on
the next call, no restart needed**.

Placeholders `{call_id}` (this call's ID), `{last_digit}` (the last IVR key before
hang-up, empty string if none), and `{digit}` (the key of an IVR digit event) are
substituted at execution time:

```
Off-hook command: /usr/local/bin/on-answer.sh {call_id}
On-hook command:  /usr/local/bin/on-hangup.sh {call_id} --last-digit {last_digit}
```

### Behavior

- **Non-blocking**: the command runs in a background (`daemon`) thread; `run()` returns
  immediately and never slows SIP signaling or the UI thread.
- **Output is discarded** and the exit code is ignored — a hook is a side-effect bypass,
  not a call-critical path.
- **Failures are swallowed and logged live**: a missing command or non-zero exit only shows
  up in the log panel as `[HOOK][ERROR] …` and never interrupts the call.
- Example log line: `[HOOK] 执行命令: /usr/local/bin/on-answer.sh CALL-AB12CD`.

### Platform examples

**macOS — verify off-hook with a system notification:**

```
Off-hook command: osascript -e 'display notification "摘机 {call_id}" with title "TeleFlow"'
```

**Linux — append a line to a call log:**

```
Off-hook command: echo "$(date) off-hook {call_id}" >> /var/log/teleflow-hooks.log
On-hook command:  echo "$(date) on-hook  {call_id}" >> /var/log/teleflow-hooks.log
```

**Any script:** just write the script path; TeleFlow runs it with `shell=True`, so it can
carry arguments and pipes:

```
Off-hook command: /opt/teleflow/on-answer.sh --id {call_id} | logger -t teleflow
```

### Security note

Commands run via `shell=True` from **the template you wrote in your local config**, and
they run **non-interactively**. Two points to note:

1. Only configure commands you trust; config file read/write is under the local user's
   permissions.
2. The value of `{call_id}` comes from the INVITE sent by the gateway (i.e. the peer-
   supplied call-id) and is substituted **verbatim into the command line**. On an
   untrusted network/gateway, a malicious call-id could be a shell-injection source. If the
   environment is untrusted, do not splice `{call_id}` directly into a shell command, or
   only use this feature on an intranet / trusted gateway.

---

## Development and testing

```bash
uv sync                      # install runtime + dev deps (PyQt6, edge-tts, pytest, mypy)
uv sync --extra pjsua2        # also install the vendored native pjsua2 wheel (build it first)
uv run pytest               # full suite (incl. the scripted FakeSipBackend gateway)
uv run mypy src/teleflow    # type check
```

- Tests resolve the package via `pythonpath=["src"]` and swap the SIP/audio backends for
  fake implementations, needing no display or native library (CI uses
  `QT_QPA_PLATFORM=offscreen`).
- Dependencies are managed with **uv**. `pjsua2` is vendored as a local wheel
  (`dist/`, see `docs/build-pjsua2.md`) and is an **optional extra** — `uv sync` installs the
  runtime/dev deps, and `uv sync --extra pjsua2` installs the native module once its wheel
  exists (the build script runs that for you). This keeps a plain `uv sync` green on every
  platform before the wheel is built. Windows uses the same vendored-wheel flow: build pjsua2
  from source with MSYS2/MinGW-w64 UCRT (`docs/build-pjsua2.md` §7), then `uv sync --extra pjsua2`.
- Feature work should happen in a separate git worktree, bringing that feature's
  `.scratch/<slug>` issues along into the worktree (keeping the `master` working tree
  clean); see the issue-tracking convention under `.scratch/`
  (`docs/agents/issue-tracker.md`).

## UI prototype

`prototypes/teleflow-ui-prototype.html` is an interactive HTML prototype: open "Settings"
from the tray menu to see the off-hook/on-hook command inputs; use the "Demo events"
button to trigger inbound/hang-up, and the log panel shows command execution as purple
`[HOOK]` lines.
