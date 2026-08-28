"""Local HTTP RPC control channel for phone reports (ticket 05).

TeleFlow listens on ``127.0.0.1`` only and serves two endpoints:

* ``POST /v1/report`` — body ``{"text": ..., "audio_path"?: ..., "voice"?: ...,
  "target"?: ..., "caller_id"?: ...}`` with ``Authorization: Bearer <token>``.
  Triggers ``SipCoreService.start_report`` and returns ``202 {"report_id"}``.
* ``GET /v1/status`` — returns a JSON snapshot of RPC/SIP/report state.

The server runs in a background thread. ``report`` work is handed to an
injectable ``scheduler`` so production can marshal it onto the Qt main thread
(pjsua2 is not thread-safe); tests use the default direct scheduler.

Security: bound to loopback, token-authenticated. An empty configured token is
auto-generated and persisted on first start so the channel is never open
without a secret.
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from teleflow.config import ConfigStore
from teleflow.sip import ReportBusyError, SipCoreService
from teleflow.tts import TtsError

DEFAULT_RPC_PORT = 8731
_MAX_BODY = 1_000_000  # 1 MB cap on request bodies


def _make_handler(service: SipCoreService, store: ConfigStore, scheduler: Callable[[Callable[[], object]], object]):
    class _Handler(BaseHTTPRequestHandler):
        # Quiet by default; the app's logger records report steps, not HTTP hits.
        def log_message(self, *args) -> None:  # noqa: D401 - override noise
            pass

        def _token(self) -> str:
            return store.load().rpc_token

        def _authorized(self) -> bool:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return False
            supplied = auth[len("Bearer ") :].strip()
            return secrets.compare_digest(supplied, self._token())

        def _send_json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            if self.path != "/v1/status":
                self._send_json(404, {"error": "not found"})
                return
            if not self._authorized():
                self._send_json(401, {"error": "unauthorized"})
                return
            settings = store.load()
            self._send_json(
                200,
                {
                    "rpc_enabled": settings.rpc_enabled,
                    "sip_running": service.running,
                    "gateway_registered": service.registered_contact is not None,
                    "call_state": service.call_state.value,
                    "report_in_progress": service.report_in_progress,
                    "tts_voice": settings.tts_voice,
                    "ffmpeg_path": settings.ffmpeg_path,
                },
            )

        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            if self.path != "/v1/report":
                self._send_json(404, {"error": "not found"})
                return
            if not self._authorized():
                self._send_json(401, {"error": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if 0 < length <= _MAX_BODY else b""
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid json"})
                return
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "invalid json"})
                return

            text = payload.get("text")
            audio_path = payload.get("audio_path")
            text = "" if text is None else str(text)
            if not text and not audio_path:
                self._send_json(400, {"error": "text or audio_path required"})
                return

            try:
                report_id = scheduler(
                    lambda: service.start_report(
                        text,
                        audio_path=audio_path,
                        voice=payload.get("voice"),
                        target=payload.get("target"),
                        caller_id=payload.get("caller_id"),
                    )
                )
            except ReportBusyError:
                self._send_json(409, {"error": "report already in progress"})
                return
            except (RuntimeError, TtsError, ValueError, FileNotFoundError, KeyError) as exc:
                self._send_json(400, {"error": str(exc)})
                return

            self._send_json(202, {"report_id": report_id})

    return _Handler


class RpcServer:
    """Loopback HTTP control channel. Bound to ``127.0.0.1`` only."""

    def __init__(
        self,
        service: SipCoreService,
        store: ConfigStore,
        scheduler: Callable[[Callable[[], object]], object] | None = None,
    ) -> None:
        self._service = service
        self._store = store
        self._scheduler = scheduler or (lambda fn: fn())
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        settings = self._store.load()
        if not settings.rpc_enabled:
            return
        if not settings.rpc_token:
            settings.rpc_token = secrets.token_hex(16)
            self._store.save(settings)
        handler = _make_handler(self._service, self._store, self._scheduler)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", settings.rpc_port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None

    @property
    def port(self) -> int | None:
        return self._httpd.server_address[1] if self._httpd is not None else None
