# TeleFlow phone-report hook

`report_hook.py` is a thin reference hook that an external agent (e.g. WorkBuddy)
invokes on its Stop hook. It inspects the last assistant message; if that message
contains the `__PHONE_REPORT__` marker, it POSTs the message text to TeleFlow's
local RPC, which dials the desk phone and reads the text aloud.

TTS and ffmpeg transcoding happen *inside* TeleFlow, so the script only moves
text — it never synthesizes audio itself.

## Usage

```bash
# Wire it as a Stop hook; the token is read from TeleFlow's config automatically.
python examples/report_hook.py < conversation.json
```

Stdin is JSON. Recognised shapes (all fields optional):

```json
{
  "messages": [ {"role": "user", "content": "..."}, {"role": "assistant", "content": "..."} ],
  "transcript": "raw transcript",
  "transcript_path": "/path/to/transcript.txt"
}
```

- If `messages` is present, the **last assistant** message is used.
- Otherwise it falls back to `transcript` / `transcript_path`.
- If the `__PHONE_REPORT__` marker is absent, the script exits 0 and does nothing
  (safe to always run).

Override the token/URL with `--token`, `--url`, `TELEFLOW_RPC_TOKEN`, or
`TELEFLOW_RPC_URL` if you don't want it to read TeleFlow's config.

## WorkBuddy `settings.json` Stop-hook wiring

```json
{
  "hooks": {
    "Stop": [
      {
        "command": "/path/to/teleflow/.venv/bin/python /path/to/teleflow/examples/report_hook.py",
        "timeout": 30
      }
    ]
  }
}
```

No secret is embedded in the command: the token is read from TeleFlow's config
(`~/.config/teleflow/config.json`). When you save Settings in TeleFlow with an
empty token, a random token is generated and persisted there; the hook picks it
up automatically.
