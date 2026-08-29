"""Tests for the inbound IVR flow (feature teleflow-call-ivr).

Exercises the service orchestration end-to-end against the scripted SIP peer
(FakeSipBackend) and the headless TTS fake: welcome + per-digit menu playback,
empty-text skipping, the first DTMF key firing EVENT_IVR_DIGIT and stopping
listening, and {last_digit} surfaced to the on-hook command.
"""

from __future__ import annotations

from pathlib import Path

from teleflow.config import ConfigStore, Settings
from teleflow.hooks import attach_hooks
from teleflow.sip import EVENT_CALL_ENDED, EVENT_IVR_DIGIT, FakeSipBackend, SipCoreService
from teleflow.tts import FakeTtsBackend


class _RecordingHookRunner:
    """Fake HookRunner (Protocol impl) that records every run() call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def run(self, command: str, context: dict[str, str]) -> None:
        self.calls.append((command, context))


def _build(tmp_path: Path, settings: Settings) -> tuple[SipCoreService, FakeSipBackend, FakeTtsBackend]:
    store = ConfigStore(tmp_path / "c.json")
    store.save(settings)
    backend = FakeSipBackend()
    tts = FakeTtsBackend()
    service = SipCoreService(backend, store, tts=tts)
    service.start()
    return service, backend, tts


def test_ivr_plays_welcome_and_menu_in_order_skips_empty(tmp_path: Path) -> None:
    settings = Settings()
    settings.ivr_enabled = True
    settings.ivr_welcome = "欢迎"
    settings.ivr_digit_text = {"1": "菜单一", "2": "", "3": "菜单三"}
    service, backend, tts = _build(tmp_path, settings)

    received: list[tuple[str, str]] = []
    service.on(EVENT_IVR_DIGIT, lambda call_id, digit: received.append((call_id, digit)))
    ended: list[tuple[str, str]] = []
    service.on(EVENT_CALL_ENDED, lambda call_id, last_digit="": ended.append((call_id, last_digit)))

    backend.receive_invite("C1")  # plays welcome + "1" + "3", then listens
    assert backend.ivr_marked == ["C1"]
    # welcome, "1", "3" played; digit "2" has empty text and is skipped.
    assert len(backend.report_played) == 3
    backend.receive_dtmf("C1", "3")
    backend.receive_bye("C1")

    assert received == [("C1", "3")]
    assert ended == [("C1", "3")]


def test_ivr_empty_welcome_and_menu_still_listens(tmp_path: Path) -> None:
    settings = Settings(ivr_enabled=True)  # no welcome, no digit text
    service, backend, tts = _build(tmp_path, settings)
    received: list[str] = []
    service.on(EVENT_IVR_DIGIT, lambda call_id, digit: received.append(digit))

    backend.receive_invite("C1")
    assert len(backend.report_played) == 0
    backend.receive_dtmf("C1", "5")
    backend.receive_bye("C1")

    assert received == ["5"]


def test_ivr_disabled_keeps_normal_behavior(tmp_path: Path) -> None:
    settings = Settings(ivr_enabled=False, ivr_digit_text={"1": "x"}, ivr_digit_hook={"1": "k"})
    service, backend, tts = _build(tmp_path, settings)
    received: list[str] = []
    service.on(EVENT_IVR_DIGIT, lambda call_id, digit: received.append(digit))
    ended: list[str] = []
    service.on(EVENT_CALL_ENDED, lambda call_id, last_digit="": ended.append(last_digit))

    backend.receive_invite("C1")
    backend.receive_dtmf("C1", "1")  # ignored: IVR not active
    backend.receive_bye("C1")

    assert received == []
    assert ended == [""]
    assert backend.ivr_marked == []


def test_ivr_only_first_digit_triggers(tmp_path: Path) -> None:
    settings = Settings(ivr_enabled=True, ivr_digit_hook={"1": "k1 {digit}", "2": "k2 {digit}"})
    service, backend, tts = _build(tmp_path, settings)
    recorder = _RecordingHookRunner()
    attach_hooks(service, recorder, ConfigStore(tmp_path / "c.json"))

    backend.receive_invite("C1")
    backend.receive_dtmf("C1", "1")
    backend.receive_dtmf("C1", "2")  # ignored after first key
    backend.receive_bye("C1")

    digit_calls = [(c, ctx) for (c, ctx) in recorder.calls if c]
    assert ("k1 {digit}", {"call_id": "C1", "digit": "1"}) in digit_calls
    assert ("k2 {digit}", {"call_id": "C1", "digit": "2"}) not in digit_calls
    # on-hook command receives {last_digit} = "1".
    assert any(ctx.get("last_digit") == "1" for (_c, ctx) in recorder.calls)


def test_ivr_per_digit_hook_empty_skips(tmp_path: Path) -> None:
    settings = Settings(ivr_enabled=True, ivr_digit_hook={"1": ""})  # empty command
    service, backend, tts = _build(tmp_path, settings)
    recorder = _RecordingHookRunner()
    attach_hooks(service, recorder, ConfigStore(tmp_path / "c.json"))

    backend.receive_invite("C1")
    backend.receive_dtmf("C1", "1")
    backend.receive_bye("C1")

    # An empty per-digit command is not run: no call carries a {digit} context.
    assert all("digit" not in ctx for (_c, ctx) in recorder.calls)
