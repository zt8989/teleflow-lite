"""Persistent configuration for TeleFlow (ticket 01).

This module is the persistence seam: it knows how to load and save the
``Settings`` record and nothing else. It has no dependency on the UI or the SIP
core, so it is fully unit-testable without a display or a running UA.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "teleflow" / "config.json"


@dataclass
class Settings:
    """The full set of persisted TeleFlow settings.

    Field values are the defaults a fresh install should start with. A stored
    file only needs to override the fields it cares about; everything else
    falls back to these defaults on load.
    """

    sip_port: int = 5060
    playback_device_id: str = ""
    capture_device_id: str = ""
    autostart: bool = False
    start_minimized: bool = False
    log_level: str = "INFO"
    gateway_port: int = 5060
    gateway_password: str = ""
    sip_number: str = "1001"
    accounts: list[str] = field(default_factory=list)

    @classmethod
    def field_names(cls) -> frozenset[str]:
        return frozenset(f.name for f in fields(cls))


class ConfigStore:
    """Loads and saves a ``Settings`` record as JSON.

    The path is injected so the store never touches the real user config
    directory during tests. A missing or malformed file yields defaults rather
    than raising, and unknown keys in the file are ignored.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_CONFIG_PATH

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return Settings()
        if not isinstance(raw, dict):
            return Settings()
        known = {k: v for k, v in raw.items() if k in Settings.field_names()}
        return Settings(**known)

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
