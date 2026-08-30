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


def _split_sip_uri(uri: object) -> tuple[str, str, int]:
    """Split a legacy ``sip:user@host:port`` URI into (user, host, port).

    Accepts bare ``host[:port]`` forms and IPv6 ``[::1]:5060`` brackets; a
    missing user or port falls back to ("", host, 5060). Used only to migrate
    the old single-URI settings (``sip_server`` / ``report_target``).
    """
    text = str(uri or "").strip()
    if text.lower().startswith("sip:"):
        text = text[4:]
    if "@" in text:
        user, _, text = text.partition("@")
    else:
        user = ""
    if text.startswith("["):  # IPv6 literal
        host, _, rest = text[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif ":" in text:
        host, _, port = text.rpartition(":")
    else:
        host, port = text, ""
    try:
        port_num = int(port)
        if not 1 <= port_num <= 65535:
            port_num = 5060
    except ValueError:
        port_num = 5060
    return user, host, port_num


@dataclass
class Settings:
    """The full set of persisted TeleFlow settings.

    Field values are the defaults a fresh install should start with. A stored
    file only needs to override the fields it cares about; everything else
    falls back to these defaults on load.
    """

    # Local SIP transport port, as a string so it can be empty. Empty means
    # auto-detect: pjsua2 binds the first free UDP port starting at 5060 (if the
    # registrar/freeswitch already listens on 5060 the client moves on). A
    # non-empty value is a preferred port, honoured only when free; otherwise
    # the user is warned and a free port is picked automatically.
    sip_port: str = ""
    # SIP client account — TeleFlow registers to this gateway as this user.
    #   sip_host         — gateway domain or IP, e.g. "192.168.1.189".
    #   sip_server_port  — gateway SIP port (default 5060).
    #   sip_user         — extension / AOR on the gateway, e.g. "1002".
    #   sip_password     — auth password.
    sip_host: str = ""
    sip_server_port: int = 5060
    sip_user: str = ""  # AOR / auth username
    sip_password: str = ""  # auth password
    playback_device_id: str = ""
    capture_device_id: str = ""
    autostart: bool = False
    start_minimized: bool = False
    # Whether the app auto-connects to the gateway on launch (starts the SIP
    # service). Persisted on every manual start/stop so the next launch
    # restores the last service state; auto-launch falls back to stopped when
    # the config is incomplete or startup fails.
    sip_auto_connect: bool = True
    log_level: str = "INFO"
    # Hook commands (ticket 01/02): local shell commands run at call-lifecycle
    # moments, with {call_id} substituted. Empty means no hook.
    #   off_hook_cmd — when the current SIP auto-answers an incoming call (摘机).
    #   on_hook_cmd  — when the call ends, e.g. the landline hangs up (挂机).
    #                 Also receives {last_digit} (the first DTMF key pressed
    #                 during an IVR call, empty string if none).
    off_hook_cmd: str = ""
    on_hook_cmd: str = ""
    # Inbound IVR (feature teleflow-call-ivr): after auto-answer, play a welcome
    # message then a per-digit-key menu, listen for the first DTMF key to fire
    # that key's command, and pass the last key to the on-hook command. Keys are
    # the digit chars "1".."9","0" (1234567890 = all number keys, not an
    # extension). _text maps a digit to its announcement; _hook maps a digit to
    # the command run when that digit is pressed (empty => skip).
    ivr_enabled: bool = True
    ivr_welcome: str = ""
    ivr_digit_text: dict[str, str] = field(default_factory=dict)
    ivr_digit_hook: dict[str, str] = field(default_factory=dict)
    # Phone-report RPC (feature teleflow-phone-report). TeleFlow can be told by an
    # external hook to dial the desk phone and play a report; these persist that
    # control channel + synthesis settings.
    #   rpc_enabled   — whether the local HTTP control channel is on.
    #   rpc_port      — localhost port for the RPC server (bound to 127.0.0.1 only).
    #   rpc_token     — bearer token; empty => auto-generated & persisted on first run.
    #   report_host      — desk phone / FXS gateway domain or IP to dial.
    #   report_port      — SIP port of the desk phone (default 5060).
    #   report_extension — desk phone extension / AOR on that gateway, e.g. "8000".
    #   report_caller_id — caller display name for the outbound report call.
    #   report_hangup_on_eof — hang up automatically when playback finishes.
    #   tts_voice     — default edge-tts voice/timbre for report synthesis.
    #   tts_cache_ttl_seconds — TTL for the synthesized-wav cache; entries
    #       older than this are re-rendered on next use (bounds disk + refreshes).
    #   tts_retry_attempts — how many times a failing edge-tts conversion (incl.
    #       timeouts) is retried before giving up; 1 == no retry.
    #   ffmpeg_path   — external ffmpeg binary; empty => auto-discover via PATH.
    rpc_enabled: bool = True
    rpc_port: int = 8731
    rpc_token: str = ""
    report_host: str = ""
    report_port: int = 5060
    report_extension: str = ""
    report_caller_id: str = "TeleFlow"
    report_hangup_on_eof: bool = True
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_cache_ttl_seconds: int = 604800
    tts_retry_attempts: int = 3
    ffmpeg_path: str = ""


    @classmethod
    def field_names(cls) -> frozenset[str]:
        return frozenset(f.name for f in fields(cls))


# Built-in edge-tts voices offered in the settings dropdown (friendly name, ID).
# The default (zh-CN-XiaoxiaoNeural) is the first entry so a fresh config lands
# on it. A trailing "自定义…" option in the UI lets users type any other ID.
BUILTIN_TTS_VOICES: list[tuple[str, str]] = [
    ("晓晓（女）", "zh-CN-XiaoxiaoNeural"),
    ("云希（男）", "zh-CN-YunxiNeural"),
    ("云扬（男·新闻）", "zh-CN-YunyangNeural"),
    ("晓伊（女·川渝）", "zh-CN-XiaoyiNeural"),
    ("云健（男）", "zh-CN-YunjianNeural"),
    ("晓辰（女）", "zh-CN-XiaochenNeural"),
    ("云霞（女）", "zh-CN-YunxiaNeural"),
    ("云野（男）", "zh-CN-YunyeNeural"),
    ("Aria（en-US 女）", "en-US-AriaNeural"),
    ("Guy（en-US 男）", "en-US-GuyNeural"),
]


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
        # ``sip_port`` became optional ("" = auto-detect, ticket 01 of
        # sip-port-auto-detect). Normalize any stored value: the old fixed
        # default 5060 (int) is upgraded to "" so legacy configs don't pin the
        # port that collides with a co-located registrar; other values are kept
        # as strings for the auto-detect code to validate.
        if "sip_port" in known:
            stored = known["sip_port"]
            if stored in (None, "", 0):
                known["sip_port"] = ""
            elif stored == 5060:
                known["sip_port"] = ""
            else:
                known["sip_port"] = str(stored).strip()
        # Migration from the single-URI era (gateway-config-split): the gateway
        # and the report target used to be one ``sip:user@host:port`` string
        # each; they are now stored as host / port / extension fields. Parse
        # the old URIs so existing configs keep working unchanged; explicit new
        # fields in the file take precedence (setdefault).
        if "sip_server" in raw:
            _user, host, port = _split_sip_uri(raw["sip_server"])
            known.setdefault("sip_host", host)
            known.setdefault("sip_server_port", port)
        if "report_target" in raw:
            user, host, port = _split_sip_uri(raw["report_target"])
            known.setdefault("report_extension", user)
            known.setdefault("report_host", host)
            known.setdefault("report_port", port)
        # Field migration from the pre-sip-softphone schema (and the intermediate
        # ata-registration branch). The old design was a SIP *server* the gateway
        # registered to; the new design is a SIP *client* that registers to an
        # external server. The server-only fields (gateway_port, accounts,
        # ata_registrar_port) have no client equivalent and are dropped. The
        # identity/password fields map onto the new client credentials. ``sip_port``
        # is already a current field, so it carries over unchanged.
        if "sip_user" not in known:
            for legacy in ("sip_number", "ata_number"):
                if legacy in raw:
                    known["sip_user"] = raw[legacy]
                    break
        if "sip_password" not in known:
            for legacy in ("gateway_password", "ata_password"):
                if legacy in raw:
                    known["sip_password"] = raw[legacy]
                    break
        return Settings(**known)

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
