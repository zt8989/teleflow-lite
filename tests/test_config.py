"""TDD tests for the Config Store — the persistence seam of ticket 01.

These are written before the implementation exists (red), then made green by
``src/teleflow/config.py``. They assert the public contract only: load/save of
a Settings record round-trips, defaults apply on a fresh/missing file, and
partial or unknown keys merge cleanly against the defaults.
"""

from pathlib import Path

from teleflow.config import ConfigStore, Settings


def test_load_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    assert settings == Settings()
    assert settings.sip_port == 5060


def test_store_accepts_str_path(tmp_path: Path) -> None:
    # ConfigStore must coerce a str path to Path so load()/save() work whether
    # the caller passes a Path or a string (regression: str path broke .exists()).
    store = ConfigStore(str(tmp_path / "nested" / "config.json"))
    store.save(Settings(sip_port=5062))
    assert store.load().sip_port == 5062


def test_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    original = Settings(
        sip_port=5070,
        playback_device_id="speaker-1",
        capture_device_id="mic-2",
        autostart=True,
        start_minimized=True,
        log_level="DEBUG",
        gateway_port=5071,
        gateway_password="secret",
        sip_number="2002",
        accounts=["1001", "1002"],
    )
    store.save(original)
    assert store.load() == original


def test_gateway_fields_default_on_fresh_file(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    assert settings.gateway_port == 5060
    assert settings.gateway_password == ""
    assert settings.sip_number == "1001"
    assert settings.accounts == []


def test_old_file_without_new_fields_uses_defaults(tmp_path: Path) -> None:
    """A config file written by an older version (no gateway/accounts keys)
    must still load without error, falling back to the new defaults."""
    store = ConfigStore(tmp_path / "config.json")
    store.path.write_text(
        '{"sip_port": 5090, "playback_device_id": "x"}', encoding="utf-8"
    )
    loaded = store.load()
    assert loaded.sip_port == 5090
    assert loaded.playback_device_id == "x"
    assert loaded.gateway_port == 5060
    assert loaded.accounts == []
    assert loaded.sip_number == "1001"


def test_partial_file_merges_with_defaults(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.path.write_text('{"sip_port": 5080}', encoding="utf-8")
    loaded = store.load()
    assert loaded.sip_port == 5080
    assert loaded.autostart is False  # default retained
    assert loaded.log_level == "INFO"


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.path.write_text('{"sip_port": 5090, "mystery": true}', encoding="utf-8")
    loaded = store.load()
    assert loaded.sip_port == 5090
    assert not hasattr(loaded, "mystery")


def test_persists_across_two_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(Settings(sip_port=6060))
    reloaded = ConfigStore(path).load()
    assert reloaded.sip_port == 6060


def test_phone_report_fields_default_on_fresh_file(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    assert settings.rpc_enabled is True
    assert settings.rpc_port == 8731
    assert settings.rpc_token == ""
    assert settings.report_target == ""
    assert settings.report_caller_id == "TeleFlow"
    assert settings.report_hangup_on_eof is True
    assert settings.tts_voice == "zh-CN-XiaoxiaoNeural"
    assert settings.ffmpeg_path == ""


def test_phone_report_fields_round_trip(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    original = Settings(
        rpc_enabled=False,
        rpc_port=9123,
        rpc_token="s3cr3t",
        report_target="sip:8000@192.168.1.116",
        report_caller_id="WorkBuddy",
        report_hangup_on_eof=False,
        tts_voice="zh-CN-YunyangNeural",
        ffmpeg_path="/opt/homebrew/bin/ffmpeg",
    )
    store.save(original)
    assert store.load() == original


def test_old_file_without_phone_report_fields_uses_defaults(tmp_path: Path) -> None:
    """A config written before the phone-report feature must load cleanly,
    falling back to the new defaults (no exception, no breakage)."""
    store = ConfigStore(tmp_path / "config.json")
    store.path.write_text(
        '{"sip_port": 5090, "playback_device_id": "x"}', encoding="utf-8"
    )
    loaded = store.load()
    assert loaded.rpc_enabled is True
    assert loaded.rpc_port == 8731
    assert loaded.report_target == ""
    assert loaded.tts_voice == "zh-CN-XiaoxiaoNeural"
    assert loaded.ffmpeg_path == ""
