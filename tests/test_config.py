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
    )
    store.save(original)
    assert store.load() == original


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
