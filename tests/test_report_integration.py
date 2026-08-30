"""End-to-end report-path tests (ticket 08).

Drive the phone-report feature through the real local HTTP RPC using fakes:
TTS -> outbound report call -> answer -> playback -> EOF -> hangup. Asserts
state transitions and EVENT_REPORT_* events, the call-recording red line, and
the ffmpeg-missing failure path. Also exercises the example hook script as a
Stop hook would invoke it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from teleflow.config import ConfigStore
from teleflow.rpc import RpcServer
from teleflow.sip import (
    EVENT_REPORT_COMPLETED,
    EVENT_REPORT_CONNECTED,
    EVENT_REPORT_FAILED,
    EVENT_REPORT_STARTED,
    FakeSipBackend,
    SipCoreService,
)
from teleflow.tts import FakeTtsBackend, FfmpegNotFound


def _tmp_store(tmp_path: Path) -> ConfigStore:
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    settings.rpc_enabled = True
    settings.rpc_token = ""  # auto-generate on RPC start
    settings.rpc_port = 0  # ephemeral port for the test
    settings.report_host = "192.168.1.116"
    settings.report_extension = "8000"
    store.save(settings)
    return store


def _post(rpc: RpcServer, token: str, payload: dict) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{rpc.port}/v1/report"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - loopback only
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class _SpySipBackend(FakeSipBackend):
    """Fake backend that records every method invoked (red-line assertions)."""

    def __init__(self) -> None:
        super().__init__()
        self.called: list[str] = []

    def place_call(self, target: str) -> None:
        self.called.append("place_call")
        super().place_call(target)

    def place_report_call(self, target: str, wav_path: str) -> None:
        self.called.append("place_report_call")
        super().place_report_call(target, wav_path)

    def play_file_to_call(self, call_id: str, wav_path: str, *, hangup_on_eof: bool = False) -> None:
        self.called.append("play_file_to_call")
        super().play_file_to_call(call_id, wav_path, hangup_on_eof=hangup_on_eof)

    def hangup(self, call_id: str) -> None:
        self.called.append("hangup")
        super().hangup(call_id)


class _FfmpegMissingTts:
    """TtsBackend whose transcode always blows up with FfmpegNotFound."""

    def synthesize(self, text: str, voice: str) -> Path:
        return Path("/tmp/fake_report.mp3")

    def transcode(self, mp3_path: Path, wav_path: Path) -> Path:
        raise FfmpegNotFound("ffmpeg not found (test)")

    def synthesize_to_wav(self, text: str, voice: str, prefix: str = "ivr") -> Path:
        raise FfmpegNotFound("ffmpeg not found (test)")


def test_hook_like_report_drives_full_flow(tmp_path: Path) -> None:
    store = _tmp_store(tmp_path)
    backend = FakeSipBackend()
    tts = FakeTtsBackend()
    service = SipCoreService(backend, store, tts=tts)
    collected: list[tuple[str, dict]] = []
    service.on(EVENT_REPORT_STARTED, lambda **kw: collected.append(("started", kw)))
    service.on(EVENT_REPORT_CONNECTED, lambda **kw: collected.append(("connected", kw)))
    service.on(EVENT_REPORT_COMPLETED, lambda **kw: collected.append(("completed", kw)))
    service.start()
    rpc = RpcServer(service, store)
    rpc.start()
    try:
        token = store.load().rpc_token
        assert token, "RPC token should have been auto-generated"

        status, body = _post(rpc, token, {"text": "这是测试汇报内容"})
        assert status == 202, body
        assert "report_id" in body

        # Outbound report call placed to the configured desk phone.
        assert backend.report_calls, "no outbound report call was placed"
        target, wav = backend.report_calls[0]
        assert target == "sip:8000@192.168.1.116:5060"
        # TTS ran (unified synthesize_to_wav entry) because no audio_path was supplied.
        assert tts.synthesized
        assert service.report_state.value == "dialing"

        # Desk phone answers -> file is played one-way into the call.
        backend.receive_report_connected("rpt-1")
        kinds = [c[0] for c in collected]
        assert "connected" in kinds
        assert backend.report_played, "file was not played into the call"
        assert service.report_state.value == "playing"

        # Playback finishes -> hang up -> completed + reset.
        backend.receive_report_playback_done("rpt-1")
        assert "completed" in [c[0] for c in collected]
        assert "rpt-1" in backend.hung_up
        assert service.report_state.value == "completed"
        assert service.report_in_progress is False
    finally:
        rpc.stop()


def test_report_red_line_never_records_audio(tmp_path: Path) -> None:
    store = _tmp_store(tmp_path)
    backend = _SpySipBackend()
    service = SipCoreService(backend, store, tts=FakeTtsBackend())
    service.start()
    rpc = RpcServer(service, store)
    rpc.start()
    try:
        token = store.load().rpc_token
        status, body = _post(rpc, token, {"text": "汇报内容"})
        assert status == 202, body
        backend.receive_report_connected("rpt-1")
        backend.receive_report_playback_done("rpt-1")

        # Red line: the report uses ONLY the one-way report call path; no normal
        # two-way call is placed, and nothing resembling a recorder / capture
        # stage is ever invoked. Playing a synthesized file is allowed; recording
        # a call is not.
        assert "place_call" not in backend.called
        assert "play_file_to_call" in backend.called
        assert not any("record" in c or "capture" in c for c in backend.called)
        assert backend.answered == []  # no incoming call was answered/bridged
    finally:
        rpc.stop()


def test_ffmpeg_missing_via_rpc_returns_400_and_recovers(tmp_path: Path) -> None:
    store = _tmp_store(tmp_path)
    backend = FakeSipBackend()
    service = SipCoreService(backend, store, tts=_FfmpegMissingTts())
    service.start()
    rpc = RpcServer(service, store)
    rpc.start()
    try:
        token = store.load().rpc_token
        status, body = _post(rpc, token, {"text": "汇报内容"})
        assert status == 400, body
        assert "ffmpeg" in body.get("error", "").lower()
        # The failure did not crash the app; the report slot is free again.
        assert service.report_in_progress is False

        # A subsequent, working request still succeeds (the slot was reset).
        service._tts = FakeTtsBackend()
        status2, _ = _post(rpc, token, {"text": "汇报内容 2"})
        assert status2 == 202
    finally:
        rpc.stop()


def test_report_call_failure_resets_slot(tmp_path: Path) -> None:
    """A report call that never connects (busy / no answer / rejected) must not
    wedge the report slot: ``report_disconnected`` resets it so the next
    /v1/report works instead of 409-ing forever."""
    store = _tmp_store(tmp_path)
    backend = FakeSipBackend()
    service = SipCoreService(backend, store, tts=FakeTtsBackend())
    collected: list[dict] = []
    service.on(EVENT_REPORT_FAILED, lambda **kw: collected.append(kw))
    service.start()
    rpc = RpcServer(service, store)
    rpc.start()
    try:
        token = store.load().rpc_token
        status, body = _post(rpc, token, {"text": "汇报内容"})
        assert status == 202, body
        assert service.report_in_progress is True

        # Desk phone never answers; the call tears down before any media came
        # up. No "bye" event fires for report calls (by design), so the service
        # must reset on report_disconnected.
        backend.receive_report_disconnected("rpt-1")
        assert service.report_in_progress is False
        assert collected and collected[0]["reason"] == "call_failed"

        # A subsequent report succeeds again (the slot was freed).
        status2, _ = _post(rpc, token, {"text": "汇报内容 2"})
        assert status2 == 202
    finally:
        rpc.stop()


def test_report_eof_resets_before_deferred_hangup(tmp_path: Path) -> None:
    """The EOF handler must free the report slot immediately even when the
    hangup itself is deferred (production marshals it onto the Qt main thread —
    pjsua2 deadlocks if hangup re-enters from the EOF callback thread, which
    used to leave report_in_progress stuck true after every report)."""
    store = _tmp_store(tmp_path)
    backend = FakeSipBackend()
    service = SipCoreService(backend, store, tts=FakeTtsBackend())
    collected: list[tuple[str, dict]] = []
    service.on(EVENT_REPORT_COMPLETED, lambda **kw: collected.append(("completed", kw)))
    deferred: list = []
    service._defer = deferred.append  # non-blocking, like MainWindow.gui
    service.start()
    rpc = RpcServer(service, store)
    rpc.start()
    try:
        token = store.load().rpc_token
        status, _ = _post(rpc, token, {"text": "汇报内容"})
        assert status == 202
        backend.receive_report_connected("rpt-1")
        backend.receive_report_playback_done("rpt-1")

        # Slot freed and event delivered immediately, hangup still queued.
        assert service.report_in_progress is False
        assert collected and collected[0][0] == "completed"
        assert backend.hung_up == []

        # The deferred hangup runs later (Qt main thread in production).
        for fn in deferred:
            fn()
        assert "rpt-1" in backend.hung_up
    finally:
        rpc.stop()


class _NoPlaybackBackend(FakeSipBackend):
    """Backend whose media never comes up for the connected report call."""

    def play_file_to_call(self, call_id: str, wav_path: str, *, hangup_on_eof: bool = False) -> bool:  # noqa: ARG002
        return False


def test_report_playback_unavailable_fails_and_resets(tmp_path: Path) -> None:
    """If the connected report call's media can't be played, the report fails
    explicitly (and hangs up) instead of waiting forever for an EOF."""
    store = _tmp_store(tmp_path)
    backend = _NoPlaybackBackend()
    service = SipCoreService(backend, store, tts=FakeTtsBackend())
    collected: list[dict] = []
    service.on(EVENT_REPORT_FAILED, lambda **kw: collected.append(kw))
    service.start()
    rpc = RpcServer(service, store)
    rpc.start()
    try:
        token = store.load().rpc_token
        status, body = _post(rpc, token, {"text": "汇报内容"})
        assert status == 202, body
        backend.receive_report_connected("rpt-1")
        assert service.report_in_progress is False
        assert collected and collected[0]["reason"] == "playback_unavailable"
        assert "rpt-1" in backend.hung_up
    finally:
        rpc.stop()


def test_edge_tts_missing_ffmpeg_raises_clear_error() -> None:
    from teleflow.tts import EdgeTtsBackend

    tts = EdgeTtsBackend(ffmpeg_path="/no/such/ffmpeg-binary")
    try:
        tts._ffmpeg_bin()
        raise AssertionError("expected FfmpegNotFound")
    except FfmpegNotFound:
        pass


def _run_hook(tmp_path: Path, payload: dict, *, expect_report: bool) -> None:
    store = _tmp_store(tmp_path)
    backend = FakeSipBackend()
    tts = FakeTtsBackend()
    service = SipCoreService(backend, store, tts=tts)
    service.start()
    rpc = RpcServer(service, store)
    rpc.start()
    try:
        token = store.load().rpc_token
        url = f"http://127.0.0.1:{rpc.port}/v1/report"
        script = Path(__file__).resolve().parents[1] / "examples" / "report_hook.py"
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        proc = subprocess.run(
            [sys.executable, str(script), "--url", url, "--token", token],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        if expect_report:
            assert backend.report_calls, proc.stderr
            # The marker is stripped before the text reaches TTS.
            assert "__PHONE_REPORT__" not in tts.synthesized[0][0]
        else:
            assert backend.report_calls == []
    finally:
        rpc.stop()


def test_hook_script_posts_on_marker(tmp_path: Path) -> None:
    _run_hook(
        tmp_path,
        {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "__PHONE_REPORT__ 这是来自助手的汇报内容。"},
            ]
        },
        expect_report=True,
    )


def test_hook_script_no_marker_exits_clean(tmp_path: Path) -> None:
    _run_hook(
        tmp_path,
        {"messages": [{"role": "assistant", "content": "普通消息，没有标记"}]},
        expect_report=False,
    )


def test_notify_phone_script_decodes_utf8_stdin_like_workbuddy(tmp_path: Path) -> None:
    """notify_phone.py is the WorkBuddy Stop hook: its stdin carries UTF-8
    bytes and the child has no PYTHONUTF8/LANG env (a text-mode read would
    decode with GBK on zh-CN Windows and garble the message). Simulate that
    spawn: process-local fake RPC + a mock ~/.config/teleflow config + a
    stripped environment."""
    store = _tmp_store(tmp_path)
    backend = FakeSipBackend()
    tts = FakeTtsBackend()
    service = SipCoreService(backend, store, tts=tts)
    service.start()
    rpc = RpcServer(service, store)
    rpc.start()
    try:
        # RpcServer.start auto-generated and persisted the token; write the
        # config file the hook script reads from its (faked) home directory.
        conf_dir = tmp_path / "home" / ".config" / "teleflow"
        conf_dir.mkdir(parents=True)
        (conf_dir / "config.json").write_text(
            json.dumps({"rpc_port": rpc.port, "rpc_token": store.load().rpc_token}),
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "USERPROFILE": str(tmp_path / "home"),
            "HOME": str(tmp_path / "home"),
        }
        for key in ("PYTHONUTF8", "PYTHONIOENCODING", "LANG", "LC_ALL", "LC_CTYPE"):
            env.pop(key, None)
        env["TELEFLOW_HOOK_DEBUG_LOG"] = str(tmp_path / "hook_debug.log")
        script = Path(__file__).resolve().parents[1] / "examples" / "notify_phone.py"
        payload = json.dumps(
            {"last_assistant_message": "宁波天气 __PHONE_REPORT__ 汇报正文。"},
            ensure_ascii=False,
        )
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=payload.encode("utf-8"),  # WorkBuddy (Node) writes UTF-8 to stdin
            capture_output=True,
            env=env,
        )
        err = proc.stderr.decode("utf-8", errors="replace")
        assert proc.returncode == 0, err
        assert backend.report_calls, err
        synthesized = tts.synthesized[0][0]
        assert "宁波天气" in synthesized  # decoded clean, not GBK mojibake
        assert "__PHONE_REPORT__" not in synthesized
    finally:
        rpc.stop()
