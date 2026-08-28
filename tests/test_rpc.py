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
    s.report_target = "sip:8000@192.168.1.116"
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
    assert backend.report_calls[0][0] == "sip:8000@192.168.1.116"
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
