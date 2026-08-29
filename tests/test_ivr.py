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
    # The IVR call is always bridged two-way, so there is no "exit" gate: the
    # on-hook command fires on every hang-up, carrying the last pressed digit.
    on_hook_ctx = [ctx for (_c, ctx) in recorder.calls if "last_digit" in ctx]
    assert on_hook_ctx == [{"call_id": "C1", "last_digit": "1"}]


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


def test_on_hook_fires_on_every_hangup_with_last_digit(tmp_path: Path) -> None:
    # Regression: with the IVR call always bridged two-way there is no "exit"
    # gate, so the on-hook command must fire on every hang-up, carrying the last
    # IVR digit pressed (or "" if none was pressed).
    settings = Settings(ivr_enabled=True, off_hook_cmd="", on_hook_cmd="STOP_HOOK")
    service, backend, tts = _build(tmp_path, settings)
    recorder = _RecordingHookRunner()
    attach_hooks(service, recorder, ConfigStore(tmp_path / "c.json"))

    # No digit pressed -> on-hook fires with last_digit "".
    backend.receive_invite("C1")
    backend.receive_bye("C1")
    on_hook = [ctx for (c, ctx) in recorder.calls if c == "STOP_HOOK"]
    assert on_hook == [{"call_id": "C1", "last_digit": ""}]

    # Pressed "1" then hung up -> on-hook fires with last_digit "1".
    backend.receive_invite("C2")
    backend.receive_dtmf("C2", "1")
    backend.receive_bye("C2")
    on_hook = [ctx for (c, ctx) in recorder.calls if c == "STOP_HOOK"]
    assert on_hook == [
        {"call_id": "C1", "last_digit": ""},
        {"call_id": "C2", "last_digit": "1"},
    ]


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


class _HoldPlaybackBackend(FakeSipBackend):
    """Fake whose one-way playback stays open until the test ends it.

    The stock fake fires ``playback_done`` synchronously, so the menu always
    drains before a key can arrive. The real pjsua2 backend keeps delivering
    ``onDtmfDigit`` while a file plays, so a key *can* arrive mid-announcement;
    this fake models that by never auto-ending a play.
    """

    def play_file_to_call(self, call_id: str, wav_path: str, *, hangup_on_eof: bool = False) -> bool:
        self.report_played.append((call_id, wav_path))
        return True


def test_ivr_digit_during_playback_fires_immediately(tmp_path: Path) -> None:
    # Regression: a DTMF key pressed while a prompt is still announcing must be
    # honoured at once (barge-in) instead of being dropped until the whole menu
    # finishes playing.
    settings = Settings(ivr_enabled=True, ivr_welcome="欢迎", ivr_digit_text={"1": "菜单一"})
    store = ConfigStore(tmp_path / "c.json")
    store.save(settings)
    backend = _HoldPlaybackBackend()
    service = SipCoreService(backend, store, tts=FakeTtsBackend())
    service.start()

    received: list[tuple[str, str]] = []
    service.on(EVENT_IVR_DIGIT, lambda call_id, digit: received.append((call_id, digit)))

    backend.receive_invite("C1")  # welcome starts announcing and stays open
    assert len(backend.report_played) == 1
    assert service._ivr_listening is False  # still mid-announcement

    backend.receive_dtmf("C1", "1")  # pressed while the welcome is still playing

    assert received == [("C1", "1")]
    assert service._ivr_queue == []  # remaining menu items canceled
    assert backend.stopped_playback == ["C1"]  # current prompt stopped
    assert len(backend.report_played) == 1  # menu tail never played afterwards

    backend.receive_dtmf("C1", "2")  # still first-key-only
    backend.receive_bye("C1")
    assert received == [("C1", "1")]


def test_ivr_digit_during_playback_no_auto_eof_chain(tmp_path: Path) -> None:
    # After a barge-in key, a late playback_done from the canceled prompt must
    # not advance the (now empty) menu chain or start any further playback.
    settings = Settings(ivr_enabled=True, ivr_digit_text={"1": "菜单一"})
    store = ConfigStore(tmp_path / "c.json")
    store.save(settings)
    backend = _HoldPlaybackBackend()
    service = SipCoreService(backend, store, tts=FakeTtsBackend())
    service.start()

    backend.receive_invite("C1")  # "1" prompt announcing
    backend.receive_dtmf("C1", "1")  # barge-in: queue canceled, playback stopped
    assert len(backend.report_played) == 1

    backend.receive_playback_done("C1")  # stray EOF from the stopped player
    assert len(backend.report_played) == 1

