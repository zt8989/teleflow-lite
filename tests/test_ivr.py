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
    # last_digit is "1", so the on-hook (stop Vibe Coding) guard skips it —
    # no on-hook context with last_digit is emitted.
    assert not any("last_digit" in ctx for (_c, ctx) in recorder.calls)


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


def test_on_hook_sends_keys_only_when_last_digit_is_zero(tmp_path: Path) -> None:
    # Regression: on-hook must fire its command (Ctrl+D+Enter to stop Vibe
    # Coding) only when the last IVR digit was "0". The guard is enforced in the
    # app, not as a shell `[ ... ]` test — the latter silently failed under
    # Windows cmd.exe, so the hangup keys were never sent.
    settings = Settings(ivr_enabled=True, off_hook_cmd="", on_hook_cmd="STOP_HOOK")
    service, backend, tts = _build(tmp_path, settings)
    recorder = _RecordingHookRunner()
    attach_hooks(service, recorder, ConfigStore(tmp_path / "c.json"))

    # No digit pressed -> on-hook must NOT fire.
    backend.receive_invite("C1")
    backend.receive_bye("C1")
    assert not any(c == "STOP_HOOK" for (c, _ctx) in recorder.calls)

    # Pressed "1" then hung up -> last_digit="1" -> on-hook must NOT fire.
    backend.receive_invite("C2")
    backend.receive_dtmf("C2", "1")
    backend.receive_bye("C2")
    assert not any(c == "STOP_HOOK" for (c, _ctx) in recorder.calls)

    # Pressed "0" then hung up -> last_digit="0" -> on-hook fires.
    backend.receive_invite("C3")
    backend.receive_dtmf("C3", "0")
    backend.receive_bye("C3")
    on_hook_calls = [(c, ctx) for (c, ctx) in recorder.calls if c == "STOP_HOOK"]
    assert len(on_hook_calls) == 1
    assert on_hook_calls[0][1]["last_digit"] == "0"
    assert on_hook_calls[0][1]["call_id"] == "C3"


def test_pressing_zero_exits_ivr_to_two_way_call(tmp_path: Path) -> None:
    # Pressing the Vibe Coding key (0) must end the one-way IVR menu and restore
    # a normal two-way call, otherwise the mic stays suppressed and nothing is
    # heard upstream (WorkBuddy can't detect the caller's voice). The call stays
    # connected so the conversation continues; only IVR mode is left.
    settings = Settings(
        ivr_enabled=True,
        ivr_digit_text={"0": "开始 Vibe Coding"},
        ivr_digit_hook={"0": "START_VIBE"},
    )
    service, backend, tts = _build(tmp_path, settings)
    received: list[str] = []
    service.on(EVENT_IVR_DIGIT, lambda call_id, digit: received.append(digit))

    backend.receive_invite("C1")
    assert backend.ivr_marked == ["C1"]
    assert service._ivr_active is True

    backend.receive_dtmf("C1", "0")  # start Vibe Coding
    assert backend.ivr_unmarked == ["C1"]  # backend told to re-bridge two-way
    assert service._ivr_active is False  # IVR menu mode ended
    assert service._ivr_call_id is None
    # Call remains connected (not hung up) so the conversation can continue.
    assert service.active_call_id == "C1"
    # The per-digit hook still fired (sends Ctrl+D to WorkBuddy).
    assert received == ["0"]


def test_ivr_exit_digit_is_configurable(tmp_path: Path) -> None:
    # The IVR-exit / bridge key must come from settings (ivr_exit_digit), not be
    # hard-coded to "0". Here "7" is the bridge key; pressing "0" must NOT exit
    # IVR (it just replays the menu via its hook), pressing "7" must.
    settings = Settings(
        ivr_enabled=True,
        ivr_exit_digit="7",
        ivr_digit_text={"7": "x", "0": "y"},
        ivr_digit_hook={"7": "K7", "0": "K0"},
    )
    service, backend, tts = _build(tmp_path, settings)

    backend.receive_invite("C1")
    backend.receive_dtmf("C1", "7")  # configured bridge key
    assert backend.ivr_unmarked == ["C1"]
    assert service._ivr_active is False

    service2, backend2, _ = _build(tmp_path, settings)
    backend2.receive_invite("C2")
    backend2.receive_dtmf("C2", "0")  # not the bridge key -> stays in IVR
    assert backend2.ivr_unmarked == []  # unchanged
    assert service2._ivr_active is True


def test_play_to_call_synthesizes_and_plays_into_active_call(tmp_path: Path) -> None:
    settings = Settings(ivr_enabled=True, ivr_welcome="欢迎")
    service, backend, tts = _build(tmp_path, settings)

    backend.receive_invite("C1")  # starts IVR, marks C1 as the active call
    before = len(backend.report_played)
    service.play_to_call("C1", text="今天天气晴")  # ad-hoc prompt via hook

    # One extra one-way playback into C1, synthesized via the (fake) TTS.
    assert len(backend.report_played) == before + 1
    assert backend.report_played[-1][0] == "C1"
    assert ("今天天气晴", settings.tts_voice) in tts.synthesized


def test_play_to_call_rejects_unknown_or_inactive_call(tmp_path: Path) -> None:
    from teleflow.sip import NoActiveCallError

    settings = Settings(ivr_enabled=True)
    service, backend, tts = _build(tmp_path, settings)

    # No call yet.
    try:
        service.play_to_call("NOPE", text="x")
        assert False, "expected NoActiveCallError"
    except NoActiveCallError:
        pass

    backend.receive_invite("C1")
    backend.receive_bye("C1")  # call ends -> no longer active
    try:
        service.play_to_call("C1", text="x")
        assert False, "expected NoActiveCallError after BYE"
    except NoActiveCallError:
        pass


def test_play_to_call_requires_text_or_audio(tmp_path: Path) -> None:
    from teleflow.sip import NoActiveCallError

    settings = Settings(ivr_enabled=True)
    service, backend, tts = _build(tmp_path, settings)
    backend.receive_invite("C1")
    try:
        service.play_to_call("C1")  # neither text nor audio_path
        assert False, "expected ValueError"
    except ValueError:
        pass
    # But an unknown call_id still wins with NoActiveCallError (checked first).
    try:
        service.play_to_call("NOPE", text="x")
        assert False
    except NoActiveCallError:
        pass


def test_replay_ivr_menu_returns_to_listening(tmp_path: Path) -> None:
    settings = Settings(
        ivr_enabled=True,
        ivr_digit_text={"1": "菜单一", "2": "菜单二", "3": ""},
    )
    service, backend, tts = _build(tmp_path, settings)
    received: list[str] = []
    service.on(EVENT_IVR_DIGIT, lambda call_id, digit: received.append(digit))

    backend.receive_invite("C1")  # plays 菜单一 + 菜单二, then listens
    backend.receive_dtmf("C1", "1")  # first key: fires, stops listening
    assert received == ["1"]
    assert not service._ivr_listening

    service.replay_ivr_menu("C1")  # back to the menu
    # Menu re-announced (菜单一 + 菜单二) into C1.
    menu_plays = [c for (c, _w) in backend.report_played if c == "C1"]
    assert len(menu_plays) >= 4  # initial 2 + replayed 2
    assert service._ivr_listening is True  # resumed listening
    assert service._ivr_digit_fired is False

    # Caller can now press another key and it fires again.
    backend.receive_dtmf("C1", "2")
    assert received == ["1", "2"]


def test_replay_ivr_menu_rejects_inactive_call(tmp_path: Path) -> None:
    from teleflow.sip import NoActiveCallError

    settings = Settings(ivr_enabled=True, ivr_digit_text={"1": "x"})
    service, backend, tts = _build(tmp_path, settings)
    try:
        service.replay_ivr_menu("NOPE")
        assert False, "expected NoActiveCallError"
    except NoActiveCallError:
        pass
    backend.receive_invite("C1")
    backend.receive_bye("C1")
    try:
        service.replay_ivr_menu("C1")
        assert False, "expected NoActiveCallError after BYE"
    except NoActiveCallError:
        pass


def test_play_to_call_surfaces_failed_start(tmp_path: Path) -> None:
    from teleflow.sip import FakeSipBackend, SipCoreService

    class _NoMediaBackend(FakeSipBackend):
        def play_file_to_call(self, call_id, wav_path, *, hangup_on_eof=False):
            return False  # simulate media never becoming active

    settings = Settings(ivr_enabled=True)
    store = ConfigStore(tmp_path / "c.json")
    store.save(settings)
    backend = _NoMediaBackend()
    service = SipCoreService(backend, store, tts=FakeTtsBackend())
    service.start()
    backend.receive_invite("C1")  # active call, but backend can't play
    try:
        service.play_to_call("C1", text="x")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "could not start" in str(exc)


def test_ivr_defers_playback_until_media_active(tmp_path: Path) -> None:
    # Regression: on the real pjsua2 backend the audio media isn't ACTIVE at
    # answer time, so the first IVR play fails. The call must NOT lose the
    # welcome/menu (it used to pop-and-drop), and must (re)start once the
    # backend signals the media is up.
    from teleflow.sip import FakeSipBackend, SipCoreService

    class _DelayedMediaBackend(FakeSipBackend):
        def __init__(self) -> None:
            super().__init__()
            self.media_ready = False

        def play_file_to_call(self, call_id, wav_path, *, hangup_on_eof=False):
            if not self.media_ready:
                return False  # media not ACTIVE yet, like early pjsua2
            return super().play_file_to_call(call_id, wav_path, hangup_on_eof=hangup_on_eof)

    settings = Settings(ivr_enabled=True, ivr_welcome="欢迎", ivr_digit_text={"1": "菜单一"})
    store = ConfigStore(tmp_path / "c.json")
    store.save(settings)
    backend = _DelayedMediaBackend()
    tts = FakeTtsBackend()
    service = SipCoreService(backend, store, tts=tts)
    service.start()

    backend.receive_invite("C1")
    # At answer time the menu is queued but NOT played (media not up yet).
    assert backend.ivr_marked == ["C1"]
    assert backend.report_played == []
    assert len(service._ivr_queue) == 2  # welcome + "1" still queued
    assert service._ivr_started is False

    # Backend signals the audio media is now ACTIVE (onCallMediaState for IVR).
    backend.media_ready = True
    service._dispatch("call_media_active", {"call_id": "C1"})

    # Now the welcome + menu play in order, and listening resumes.
    assert len(backend.report_played) == 2
    assert service._ivr_started is True
    assert service._ivr_listening is True
    # Each digit prompt is announced as "{text} 请按{digit}" so the caller knows
    # the key to press.
    assert ("欢迎", settings.tts_voice) in tts.synthesized
    assert ("菜单一 请按1", settings.tts_voice) in tts.synthesized
    assert ("菜单一", settings.tts_voice) not in tts.synthesized

    # A spurious second media-active event must not replay anything.
    service._dispatch("call_media_active", {"call_id": "C1"})
    assert len(backend.report_played) == 2

