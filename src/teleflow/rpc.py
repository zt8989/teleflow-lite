"""Local HTTP RPC control channel for phone reports (ticket 05) and inbound
IVR hooks (tickets 07/08).

TeleFlow listens on ``127.0.0.1`` only and serves these endpoints:

* ``POST /v1/report`` — body ``{"text": ..., "audio_path"?: ..., "voice"?: ...,
  "target"?: ..., "caller_id"?: ...}`` with ``Authorization: Bearer <token>``.
  Triggers ``SipCoreService.start_report`` and returns ``202 {"report_id"}``.
* ``POST /v1/play`` — body ``{"call_id": ..., "text"?: ..., "audio_path"?: ...,
  "voice"?: ..., "hangup_on_eof"?: ...}``. Plays a prompt into the live inbound
  call identified by ``call_id`` (``SipCoreService.play_to_call``); returns
  ``202 {"call_id"}`` or ``404`` when there is no active call.
* ``POST /v1/ivr/replay`` — body ``{"call_id": ...}``. Re-announces the IVR
  digit menu and resumes listening (``SipCoreService.replay_ivr_menu``); returns
  ``202 {"call_id"}`` or ``404`` when the call is not an active IVR call.
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
from teleflow.sip import NoActiveCallError, ReportBusyError, SipCoreService
from teleflow.tts import TtsError

DEFAULT_RPC_PORT = 8731
_MAX_BODY = 1_000_000  # 1 MB cap on request bodies


def _make_handler(
    service: SipCoreService,
    store: ConfigStore,
    scheduler: Callable[[Callable[[], object]], object],
    log: Callable[[str], None] | None = None,
):
    class _Handler(BaseHTTPRequestHandler):
        # We log our own clean per-request lines via the unified log API (see
        # _send_json), so keep BaseHTTPRequestHandler's stderr chatter off.
        def log_message(self, *args) -> None:  # noqa: D401 - override noise
            pass

        def _rpc_log(self, line: str) -> None:
            if log is not None:
                log(line)

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
            # Every RPC response lands in the unified log (file + UI panel).
            # Only method/path/status are recorded — never headers or the body,
            # so the bearer token can't leak into the log.
            marker = "[WARN]" if code >= 400 else ""
            self._rpc_log(f"[RPC]{marker} {self.command} {self.path} -> {code}")

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
                    "active_call_id": service.active_call_id,
                    "report_in_progress": service.report_in_progress,
                    "tts_voice": settings.tts_voice,
                    "ffmpeg_path": settings.ffmpeg_path,
                },
            )

        def _do_report_reset(self) -> None:
            """``POST /v1/report/reset`` (aliases /abort, /cancel) — force-clear a
            wedged report slot so /v1/report can be retried without restarting."""
            if not self._authorized():
                self._send_json(401, {"error": "unauthorized"})
                return
            service.reset_report()
            self._send_json(
                200,
                {
                    "reset": True,
                    "report_in_progress": service.report_in_progress,
                },
            )

        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            if self.path == "/v1/play":
                self._do_play()
                return
            if self.path == "/v1/ivr/replay":
                self._do_ivr_replay()
                return
            if self.path in ("/v1/report/reset", "/v1/report/abort", "/v1/report/cancel"):
                self._do_report_reset()
                return
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

        def _do_play(self) -> None:
            """``POST /v1/play`` — play a prompt into a live inbound call.

            Body: ``{"call_id", "text"?, "audio_path"?, "voice"?, "hangup_on_eof"?}``.
            Maps a missing/inactive call to 404, missing text+audio to 400, and
            synthesis/file errors to 400; success returns 202 ``{"call_id"}``.
            """
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
            call_id = payload.get("call_id")
            if not call_id or not str(call_id).strip():
                self._send_json(400, {"error": "call_id required"})
                return
            call_id = str(call_id).strip()
            text = payload.get("text")
            audio_path = payload.get("audio_path")
            text = "" if text is None else str(text)
            if not text and not audio_path:
                self._send_json(400, {"error": "text or audio_path required"})
                return
            try:
                scheduler(
                    lambda: service.play_to_call(
                        call_id,
                        text=text or None,
                        audio_path=audio_path,
                        voice=payload.get("voice"),
                        hangup_on_eof=bool(payload.get("hangup_on_eof", False)),
                    )
                )
            except NoActiveCallError:
                self._send_json(404, {"error": "no active call"})
                return
            except (RuntimeError, TtsError, ValueError, FileNotFoundError, KeyError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(202, {"call_id": call_id})

        def _do_ivr_replay(self) -> None:
            """``POST /v1/ivr/replay`` — return an active IVR call to its menu.

            Body: ``{"call_id"}``. Maps a missing/inactive IVR call to 404 and
            other errors to 400; success returns 202 ``{"call_id"}``.
            """
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
            call_id = payload.get("call_id")
            if not call_id or not str(call_id).strip():
                self._send_json(400, {"error": "call_id required"})
                return
            call_id = str(call_id).strip()
            try:
                scheduler(lambda: service.replay_ivr_menu(call_id))
            except NoActiveCallError:
                self._send_json(404, {"error": "no active call"})
                return
            except (RuntimeError, ValueError, KeyError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(202, {"call_id": call_id})

    return _Handler


class RpcServer:
    """Loopback HTTP control channel. Bound to ``127.0.0.1`` only."""

    def __init__(
        self,
        service: SipCoreService,
        store: ConfigStore,
        scheduler: Callable[[Callable[[], object]], object] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._service = service
        self._store = store
        self._scheduler = scheduler or (lambda fn: fn())
        self._log = log
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        settings = self._store.load()
        if not settings.rpc_enabled:
            return
        if not settings.rpc_token:
            settings.rpc_token = secrets.token_hex(16)
            self._store.save(settings)
        # Per-request [RPC] lines are emitted by the handler via ``log`` (the
        # unified logger; file + UI panel).
        handler = _make_handler(self._service, self._store, self._scheduler, log=self._log)
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
