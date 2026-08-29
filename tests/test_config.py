"""TDD tests for the Config Store — the persistence seam of ticket 01.

These are written before the implementation exists (red), then made green by
``src/teleflow/config.py``. They assert the public contract only: load/save of
a Settings record round-trips, defaults apply on a fresh/missing file, and
partial or unknown keys merge cleanly against the defaults.
"""

import json
from pathlib import Path

from teleflow.config import ConfigStore, Settings


def test_load_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    assert settings == Settings()
    assert settings.sip_port == ""
    assert settings.sip_server == ""
    assert settings.sip_user == ""
    assert settings.sip_password == ""


def test_store_accepts_str_path(tmp_path: Path) -> None:
    # ConfigStore must coerce a str path to Path so load()/save() work whether
    # the caller passes a Path or a string (regression: str path broke .exists()).
    store = ConfigStore(str(tmp_path / "nested" / "config.json"))
    store.save(Settings(sip_port="5062"))
    assert store.load().sip_port == "5062"


def test_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    original = Settings(
        sip_port="5070",
        playback_device_id="speaker-1",
        capture_device_id="mic-2",
        autostart=True,
        start_minimized=True,
        log_level="DEBUG",
        sip_server="sip:proxy.example.com:5060",
        sip_user="2002",
        sip_password="secret",
    )
    store.save(original)
    assert store.load() == original


def test_sip_client_fields_default_on_fresh_file(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    assert settings.sip_port == ""  # "" = auto-detect
    assert settings.sip_server == ""
    assert settings.sip_user == ""
    assert settings.sip_password == ""
    assert settings.sip_auto_connect is True  # auto-connect gateway on launch


def test_sip_auto_connect_round_trips(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(Settings(sip_auto_connect=False))
    assert store.load().sip_auto_connect is False
    store.save(Settings(sip_auto_connect=True))
    assert store.load().sip_auto_connect is True


def test_old_file_without_new_fields_uses_defaults(tmp_path: Path) -> None:
    """A config file written by an older version (pre-sip-softphone) must still
    load, carrying ``sip_port`` over as the local transport and dropping the
    retired server-only fields (gateway_port, accounts, ata_registrar_port)."""
    store = ConfigStore(tmp_path / "config.json")
    store.path.write_text(
        '{"sip_port": 5090, "playback_device_id": "x"}', encoding="utf-8"
    )
    loaded = store.load()
    assert loaded.sip_port == "5090"  # explicit old port carries over (str)
    assert loaded.playback_device_id == "x"
    assert loaded.sip_user == ""  # sip_number no longer auto-defaults


def test_migration_maps_gateway_fields_to_client_credentials(tmp_path: Path) -> None:
    """A pre-sip-softphone config used gateway_password / sip_number for the
    gateway identity. These must migrate onto the new client sip_user /
    sip_password; the server-only gateway_port / accounts are dropped."""
    store = ConfigStore(tmp_path / "config.json")
    store.path.write_text(
        json.dumps(
            {
                "sip_port": 5060,
                "gateway_port": 5062,
                "gateway_password": "old-secret",
                "sip_number": "2001",
                "accounts": ["1001@provider"],
            }
        ),
        encoding="utf-8",
    )
    loaded = store.load()
    assert loaded.sip_user == "2001"
    assert loaded.sip_password == "old-secret"
    assert loaded.sip_port == ""  # legacy fixed default 5060 upgrades to auto
    assert not hasattr(loaded, "gateway_port")  # retired, not carried over
    assert not hasattr(loaded, "accounts")


def test_partial_file_merges_with_defaults(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.path.write_text('{"sip_port": 5080}', encoding="utf-8")
    loaded = store.load()
    assert loaded.sip_port == "5080"  # explicit port migrates to local transport
    assert loaded.autostart is False  # default retained
    assert loaded.log_level == "INFO"


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.path.write_text('{"sip_port": 5090, "mystery": true}', encoding="utf-8")
    loaded = store.load()
    assert loaded.sip_port == "5090"  # explicit port migrates to local transport
    assert not hasattr(loaded, "mystery")


def test_legacy_default_5060_upgrades_to_auto(tmp_path: Path) -> None:
    """The old fixed default (int 5060) must not pin the port that collides
    with a co-located registrar; it upgrades to "" (auto-detect)."""
    store = ConfigStore(tmp_path / "config.json")
    store.path.write_text('{"sip_port": 5060}', encoding="utf-8")
    assert store.load().sip_port == ""


def test_persists_across_two_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(Settings(sip_port="6060"))
    reloaded = ConfigStore(path).load()
    assert reloaded.sip_port == "6060"


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
