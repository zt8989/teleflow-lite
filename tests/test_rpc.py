"""TDD tests for the local HTTP RPC control channel (ticket 05).

Exercises the real HTTP server on a free loopback port with the scripted SIP
and TTS backends — no pjsua2, no network, no ffmpeg.
"""

import json
import urllib.request
from pathlib import Path

from teleflow.config import ConfigStore
from teleflow.rpc import RpcServer
from teleflow.sip import FakeSipBackend, SipCoreService
from teleflow.tts import FakeTtsBackend


def _rpc(tmp_path: Path, token="tok"):
    store = ConfigStore(tmp_path / "config.json")
    s = store.load()
    s.rpc_enabled = True
    s.rpc_token = token
    s.rpc_port = 0  # bind a free port
    s.report_host = "192.168.1.116"
    s.report_extension = "8000"
    store.save(s)
    backend = FakeSipBackend()
    tts = FakeTtsBackend()
    svc = SipCoreService(backend, store, tts=tts)
    svc.start()
    rpc = RpcServer(svc, store)
    rpc.start()
    return rpc, svc, backend, store


def _post(base: str, token: str, payload: dict):
    req = urllib.request.Request(
        base + "/v1/report",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=5)


def test_report_post_triggers_outbound_call(tmp_path: Path) -> None:
    rpc, svc, backend, _ = _rpc(tmp_path)
    with _post(f"http://127.0.0.1:{rpc.port}", "tok", {"text": "会议纪要"}) as resp:
        assert resp.status == 202
        body = json.loads(resp.read())
        assert "report_id" in body
    assert backend.report_calls, "expected an outbound report call to be placed"
    assert backend.report_calls[0][0] == "sip:8000@192.168.1.116:5060"
    assert backend.report_calls[0][1].endswith(".wav")


def test_report_requires_token(tmp_path: Path) -> None:
    rpc, _, _, _ = _rpc(tmp_path)
    req = urllib.request.Request(
        f"http://127.0.0.1:{rpc.port}/v1/report",
        data=json.dumps({"text": "x"}).encode(),
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False, "expected 401"
    except urllib.error.HTTPError as e:
        assert e.code == 401


def test_report_missing_text_and_audio_returns_400(tmp_path: Path) -> None:
    rpc, _, _, _ = _rpc(tmp_path)
    req = urllib.request.Request(
        f"http://127.0.0.1:{rpc.port}/v1/report",
        data=json.dumps({}).encode(),
        headers={"Authorization": "Bearer tok"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_report_concurrent_returns_409(tmp_path: Path) -> None:
    rpc, _, backend, _ = _rpc(tmp_path)
    with _post(f"http://127.0.0.1:{rpc.port}", "tok", {"text": "first"}) as resp:
        assert resp.status == 202
    # Second report while the first is still "active" (no answer yet).
    req = urllib.request.Request(
        f"http://127.0.0.1:{rpc.port}/v1/report",
        data=json.dumps({"text": "second"}).encode(),
        headers={"Authorization": "Bearer tok"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 409
    assert len(backend.report_calls) == 1


def test_status_requires_token_and_returns_state(tmp_path: Path) -> None:
    rpc, svc, _, _ = _rpc(tmp_path)
    # Without token -> 401.
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{rpc.port}/v1/status", timeout=5)
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 401
    # With token -> 200 + state.
    req = urllib.request.Request(
        f"http://127.0.0.1:{rpc.port}/v1/status",
        headers={"Authorization": "Bearer tok"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read())
    assert body["sip_running"] is True
    assert body["report_in_progress"] == svc.report_in_progress
    assert body["tts_voice"]


def test_status_reports_active_call_id(tmp_path: Path) -> None:
    rpc, svc, backend, _ = _rpc(tmp_path)
    # No call yet -> empty string.
    req = urllib.request.Request(
        f"http://127.0.0.1:{rpc.port}/v1/status",
        headers={"Authorization": "Bearer tok"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert json.loads(resp.read())["active_call_id"] == ""
    # After an inbound call connects, the id is exposed.
    backend.receive_invite("C1")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert json.loads(resp.read())["active_call_id"] == "C1"


def _post_path(base: str, token: str, path: str, payload: dict):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=5)


def _active_call(rpc, backend, call_id="C1"):
    """Drive an inbound IVR call so the service has a live call to target."""
    backend.receive_invite(call_id)


def test_play_requires_token(tmp_path: Path) -> None:
    rpc, _, _, _ = _rpc(tmp_path)
    req = urllib.request.Request(
        f"http://127.0.0.1:{rpc.port}/v1/play",
        data=json.dumps({"call_id": "C1", "text": "x"}).encode(),
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 401


def test_play_into_active_call_returns_202(tmp_path: Path) -> None:
    rpc, svc, backend, _ = _rpc(tmp_path)
    _active_call(rpc, backend, "C1")
    with _post_path(f"http://127.0.0.1:{rpc.port}", "tok", "/v1/play",
                    {"call_id": "C1", "text": "今天天气晴"}) as resp:
        assert resp.status == 202
        assert json.loads(resp.read()) == {"call_id": "C1"}
    assert any(c == "C1" for (c, _w) in backend.report_played)


def test_play_without_active_call_returns_404(tmp_path: Path) -> None:
    rpc, _, _, _ = _rpc(tmp_path)
    try:
        _post_path(f"http://127.0.0.1:{rpc.port}", "tok", "/v1/play",
                   {"call_id": "C1", "text": "x"})
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 404
        assert json.loads(e.read())["error"] == "no active call"


def test_play_missing_call_id_returns_400(tmp_path: Path) -> None:
    rpc, _, _, _ = _rpc(tmp_path)
    try:
        _post_path(f"http://127.0.0.1:{rpc.port}", "tok", "/v1/play", {"text": "x"})
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_play_missing_text_and_audio_returns_400(tmp_path: Path) -> None:
    rpc, _, backend, _ = _rpc(tmp_path)
    _active_call(rpc, backend, "C1")
    try:
        _post_path(f"http://127.0.0.1:{rpc.port}", "tok", "/v1/play", {"call_id": "C1"})
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_ivr_replay_requires_token(tmp_path: Path) -> None:
    rpc, _, _, _ = _rpc(tmp_path)
    req = urllib.request.Request(
        f"http://127.0.0.1:{rpc.port}/v1/ivr/replay",
        data=json.dumps({"call_id": "C1"}).encode(),
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 401


def test_ivr_replay_active_call_returns_202(tmp_path: Path) -> None:
    rpc, svc, backend, _ = _rpc(tmp_path)
    _active_call(rpc, backend, "C1")
    with _post_path(f"http://127.0.0.1:{rpc.port}", "tok", "/v1/ivr/replay",
                    {"call_id": "C1"}) as resp:
        assert resp.status == 202
        assert json.loads(resp.read()) == {"call_id": "C1"}


def test_ivr_replay_without_active_call_returns_404(tmp_path: Path) -> None:
    rpc, _, _, _ = _rpc(tmp_path)
    try:
        _post_path(f"http://127.0.0.1:{rpc.port}", "tok", "/v1/ivr/replay",
                   {"call_id": "C1"})
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 404
        assert json.loads(e.read())["error"] == "no active call"


def test_rpc_requests_log_to_unified_logger(tmp_path: Path) -> None:
    # Every RPC response is recorded via the unified log API (file + UI panel
    # get the same line): 2xx as INFO, 4xx as WARNING — and never the token.
    lines: list[str] = []
    store = ConfigStore(tmp_path / "config.json")
    s = store.load()
    s.rpc_enabled = True
    s.rpc_token = "tok"
    s.rpc_port = 0
    store.save(s)
    svc = SipCoreService(FakeSipBackend(), store, tts=FakeTtsBackend())
    svc.start()
    rpc = RpcServer(svc, store, log=lines.append)
    rpc.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{rpc.port}/v1/status",
            headers={"Authorization": "Bearer tok"},
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
        assert "[RPC] GET /v1/status -> 200" in lines

        bad = urllib.request.Request(
            f"http://127.0.0.1:{rpc.port}/v1/status",
            headers={"Authorization": "Bearer nope"},
        )
        try:
            urllib.request.urlopen(bad, timeout=5)
            assert False, "expected 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401
        assert any("[RPC][WARN] GET /v1/status -> 401" in l for l in lines)
        # The bearer token must never appear in a log line.
        assert all("tok" not in l for l in lines)
    finally:
        rpc.stop()
