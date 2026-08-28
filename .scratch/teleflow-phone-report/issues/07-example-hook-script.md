# 07 — Example hook script

**What to build:** A thin reference script an external Stop hook (e.g. WorkBuddy) can invoke: extract the last assistant message, detect the `__PHONE_REPORT__` marker, clean Markdown, then POST the text to TeleFlow's RPC. TTS and transcoding stay inside TeleFlow, so the script is minimal.

**Blocked by:** 05 — rpc-control-channel

**Status:** resolved

- [x] `examples/report_hook.py` reads the hook payload from stdin, extracts the assistant message, detects the marker, and POSTs `{ "text": ... }` with the bearer token.
- [x] It degrades gracefully on broken/missing payload fields (falls back to `transcript_path` like the user's original script).
- [x] A comment/README documents the WorkBuddy `settings.json` Stop-hook wiring (command + timeout).
