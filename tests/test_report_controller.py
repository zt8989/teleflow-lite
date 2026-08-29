"""TDD tests for the report controller state machine (ticket 04).

Driven entirely through the public ``SipCoreService`` interface against the
scripted ``FakeSipBackend`` and a ``FakeTtsBackend`` — no pjsua2, no network,
no ffmpeg. Asserts the full vertical slice: validate -> TTS -> dial -> on
answer play -> on EOF hang up, plus the failure/guard branches.
"""

from pathlib import Path

import pytest

from teleflow.config import ConfigStore
from teleflow.sip import (
    EVENT_REPORT_COMPLETED,
    EVENT_REPORT_CONNECTED,
    EVENT_REPORT_FAILED,
    EVENT_REPORT_PLAYING,
    EVENT_REPORT_STARTED,
    FakeSipBackend,
    ReportBusyError,
    ReportState,
    SipCoreService,
)
from teleflow.tts import FakeTtsBackend, TtsError


def _service(
    tmp_path,
    tts=None,
    report_host="192.168.1.116",
    report_port=5060,
    report_extension="8000",
    sip_host="",
    sip_server_port=5060,
):
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    settings.report_host = report_host
    settings.report_port = report_port
    settings.report_extension = report_extension
    settings.sip_host = sip_host
    settings.sip_server_port = sip_server_port
    store.save(settings)
    backend = FakeSipBackend()
    svc = SipCoreService(backend, store, tts=tts)
    return svc, backend, store


def test_start_report_triggers_tts_then_outbound_call(tmp_path: Path) -> None:
    tts = FakeTtsBackend()
    svc, backend, _ = _service(tmp_path, tts=tts)
    svc.start()

    report_id = svc.start_report("会议纪要")

    assert tts.synthesized == [("会议纪要", "zh-CN-XiaoxiaoNeural")]
    assert backend.report_calls == [("sip:8000@192.168.1.116:5060", str(tts._fake_wav))]
    assert svc.report_state is ReportState.DIALING
    assert svc.report_in_progress is True
    assert report_id


def test_report_connected_then_eof_hangs_up(tmp_path: Path) -> None:
    tts = FakeTtsBackend()
    svc, backend, _ = _service(tmp_path, tts=tts)
    svc.start()
    states: list[str] = []
    svc.on(EVENT_REPORT_CONNECTED, lambda call_id: states.append("connected"))
    svc.on(EVENT_REPORT_PLAYING, lambda call_id: states.append("playing"))
    completed: list[str] = []
    svc.on(EVENT_REPORT_COMPLETED, lambda report_id, call_id: completed.append(call_id))

    svc.start_report("会议纪要")
    backend.receive_report_connected("call-9")
    backend.receive_report_playback_done("call-9")

    assert states == ["connected", "playing"]
    assert backend.report_played == [("call-9", str(tts._fake_wav))]
    assert backend.hung_up == ["call-9"]
    assert completed == ["call-9"]
    assert svc.report_state is ReportState.COMPLETED
    assert svc.report_in_progress is False


def test_start_report_with_audio_path_skips_tts(tmp_path: Path) -> None:
    tts = FakeTtsBackend()
    svc, backend, _ = _service(tmp_path, tts=tts)
    svc.start()
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF")

    svc.start_report("ignored", audio_path=str(wav))

    assert tts.synthesized == []  # text path was not used
    assert backend.report_calls == [("sip:8000@192.168.1.116:5060", str(wav))]


def test_start_report_requires_running(tmp_path: Path) -> None:
    tts = FakeTtsBackend()
    svc, backend, _ = _service(tmp_path, tts=tts)  # not started
    failed: list[str] = []
    svc.on(EVENT_REPORT_FAILED, lambda reason, report_id: failed.append(reason))

    with pytest.raises(RuntimeError):
        svc.start_report("x")

    assert failed == ["sip_not_running"]
    assert backend.report_calls == []


def test_start_report_requires_extension(tmp_path: Path) -> None:
    tts = FakeTtsBackend()
    # No extension => no dialable target, even with a gateway configured.
    svc, backend, _ = _service(
        tmp_path, tts=tts, report_extension="", sip_host="192.168.1.189"
    )
    svc.start()
    failed: list[str] = []
    svc.on(EVENT_REPORT_FAILED, lambda reason, report_id: failed.append(reason))

    with pytest.raises(RuntimeError):
        svc.start_report("x")

    assert failed == ["no_target"]
    assert backend.report_calls == []


def test_start_report_defaults_to_gateway_route(tmp_path: Path) -> None:
    tts = FakeTtsBackend()
    # Only the extension + a configured gateway: routes through the gateway.
    svc, backend, _ = _service(
        tmp_path,
        tts=tts,
        report_host="",  # no 座机 address -> 走网关
        report_extension="8000",
        sip_host="192.168.1.189",
        sip_server_port=5060,
    )
    svc.start()

    svc.start_report("会议纪要")

    assert backend.report_calls == [("sip:8000@192.168.1.189:5060", str(tts._fake_wav))]


def test_start_report_desk_phone_route_defaults_port_5060(tmp_path: Path) -> None:
    tts = FakeTtsBackend()
    # 座机 address set, no port => defaults to 5060.
    svc, backend, _ = _service(
        tmp_path, tts=tts, report_host="192.168.1.116", report_port=0
    )
    svc.start()

    svc.start_report("会议纪要")

    assert backend.report_calls == [("sip:8000@192.168.1.116:5060", str(tts._fake_wav))]


def test_start_report_desk_phone_route_explicit_port(tmp_path: Path) -> None:
    tts = FakeTtsBackend()
    svc, backend, _ = _service(
        tmp_path, tts=tts, report_host="192.168.1.116", report_port=5080
    )
    svc.start()

    svc.start_report("会议纪要")

    assert backend.report_calls == [("sip:8000@192.168.1.116:5080", str(tts._fake_wav))]


def test_start_report_no_route_without_extension_or_host(tmp_path: Path) -> None:
    tts = FakeTtsBackend()
    # Extension set, but neither a 座机 address nor a gateway host => no_target.
    svc, backend, _ = _service(
        tmp_path, tts=tts, report_host="", report_extension="8000", sip_host=""
    )
    svc.start()
    failed: list[str] = []
    svc.on(EVENT_REPORT_FAILED, lambda reason, report_id: failed.append(reason))

    with pytest.raises(RuntimeError):
        svc.start_report("x")

    assert failed == ["no_target"]
    assert backend.report_calls == []


def test_start_report_missing_audio_file_fails(tmp_path: Path) -> None:
    tts = FakeTtsBackend()
    svc, backend, _ = _service(tmp_path, tts=tts)
    svc.start()
    failed: list[str] = []
    svc.on(EVENT_REPORT_FAILED, lambda reason, report_id: failed.append(reason))

    with pytest.raises(RuntimeError):
        svc.start_report("x", audio_path="/no/such/file.wav")

    assert failed == ["file_missing"]


def test_concurrency_guard_blocks_second_report(tmp_path: Path) -> None:
    tts = FakeTtsBackend()
    svc, backend, _ = _service(tmp_path, tts=tts)
    svc.start()
    svc.start_report("first")

    with pytest.raises(ReportBusyError):
        svc.start_report("second")


class _FailingTts(FakeTtsBackend):
    def synthesize(self, text: str, voice: str) -> Path:
        raise TtsError("edge-tts boom")


def test_tts_failure_reports_failed(tmp_path: Path) -> None:
    svc, backend, _ = _service(tmp_path, tts=_FailingTts())
    svc.start()
    failed: list[str] = []
    svc.on(EVENT_REPORT_FAILED, lambda reason, report_id: failed.append(reason))

    with pytest.raises(TtsError):
        svc.start_report("x")

    assert failed == ["tts"]
    assert backend.report_calls == []
