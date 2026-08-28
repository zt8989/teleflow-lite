#!/usr/bin/env python3
"""TeleFlow phone-report hook (reference implementation).

Designed to be wired as a Stop hook in an external agent (e.g. WorkBuddy):
whenever a conversation finishes, this script inspects the last assistant
message; if it carries the ``__PHONE_REPORT__`` marker, the message text is
POSTed to the local TeleFlow RPC, which then dials the desk phone and plays the
text aloud via edge-tts.

TTS and ffmpeg transcoding live inside TeleFlow, so this script is intentionally
thin: extract text -> detect marker -> POST ``{"text": ...}``.

Input contract (read from stdin as JSON; all fields optional):
    {
      "messages": [ {"role": "user"|"assistant", "content": "..."}, ... ],
      "transcript": "raw transcript string",
      "transcript_path": "/path/to/transcript.txt"
    }
If ``messages`` is present, the *last* assistant message is used.
Otherwise we fall back to ``transcript`` / ``transcript_path`` (mirrors the
user's original notify_phone.py behaviour).

Exit codes:
    0  marker absent, or report dispatched successfully (Stop hook stays happy)
    1  payload missing / unreadable
    2  RPC call failed (TeleFlow not running, bad token, no report target, ...)

WorkBuddy ``settings.json`` Stop-hook wiring (illustrative):
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
(The token is read from TeleFlow's config automatically; no secret is embedded
in the hook command. You may override with ``--token`` or ``TELEFLOW_RPC_TOKEN``.)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8731/v1/report"
MARKER = "__PHONE_REPORT__"


def _load_token_and_port() -> tuple[str, str]:
    """Resolve the RPC token and URL base.

    Precedence: CLI arg / env var -> TeleFlow config file -> empty (RPC will 401).
    """
    token = os.environ.get("TELEFLOW_RPC_TOKEN", "")
    port = "8731"
    try:
        from teleflow.config import ConfigStore  # local import keeps the script usable without the package

        settings = ConfigStore().load()
        token = token or settings.rpc_token
        port = str(settings.rpc_port)
    except Exception:  # pragma: no cover - optional integration only
        pass
    return token, port


def clean_text(text: str) -> str:
    """Strip the marker and tidy Markdown if teleflow is importable."""
    text = text.replace(MARKER, "").replace(MARKER.lower(), "")
    try:
        from teleflow.tts import clean_markdown

        return clean_markdown(text)
    except Exception:  # pragma: no cover - optional; TeleFlow cleans again anyway
        return text.strip()


def extract_assistant_text(payload: dict) -> str | None:
    """Return the last assistant message text, or None to fall back to transcript."""
    messages = payload.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict) and str(msg.get("role", "")).lower() == "assistant":
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content
                # content may be a list of {type, text} blocks (some agents)
                if isinstance(content, list):
                    parts = [
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    ]
                    joined = "\n".join(p for p in parts if p)
                    if joined.strip():
                        return joined
    return None


def read_transcript(payload: dict) -> str:
    """Fall back to a raw transcript or a transcript file path."""
    transcript = payload.get("transcript")
    if isinstance(transcript, str) and transcript.strip():
        return transcript
    path = payload.get("transcript_path")
    if isinstance(path, str) and path:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def parse_payload(raw: bytes) -> dict:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Treat a non-JSON stdin as a raw transcript blob.
        return {"transcript": raw.decode("utf-8", "replace")}
    return data if isinstance(data, dict) else {}


def post_report(url: str, token: str, text: str) -> None:
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - loopback only
        result = json.loads(resp.read().decode("utf-8"))
    print(f"[report_hook] dispatched report_id={result.get('report_id')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TeleFlow phone-report Stop hook")
    parser.add_argument("--url", default=None, help="RPC URL (default: auto from config)")
    parser.add_argument("--token", default=None, help="Bearer token (default: from config/env)")
    args = parser.parse_args(argv)

    token, port = _load_token_and_port()
    token = args.token or token
    url = args.url or os.environ.get("TELEFLOW_RPC_URL") or f"http://127.0.0.1:{port}/v1/report"

    try:
        payload = parse_payload(sys.stdin.buffer.read())
    except OSError as exc:
        print(f"[report_hook] cannot read stdin: {exc}", file=sys.stderr)
        return 1

    text = extract_assistant_text(payload)
    if text is None:
        text = read_transcript(payload)

    if MARKER not in text and MARKER.lower() not in text:
        # No marker => nothing to report. Exit cleanly so the Stop hook is happy.
        return 0

    text = clean_text(text)
    if not text:
        print("[report_hook] marker present but no text to report", file=sys.stderr)
        return 1

    if not token:
        print("[report_hook] no RPC token configured; cannot authenticate", file=sys.stderr)
        return 2

    try:
        post_report(url, token, text)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(f"[report_hook] RPC error {exc.code}: {detail}", file=sys.stderr)
        return 2
    except urllib.error.URLError as exc:
        print(f"[report_hook] cannot reach TeleFlow RPC: {exc.reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
