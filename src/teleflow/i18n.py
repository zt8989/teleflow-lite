"""Internationalization (i18n) for TeleFlow — pure-Python, Qt-free.

UI strings are looked up by stable message keys via ``tr(key, **kwargs)``. Two
locales ship in ``locales/``: ``en`` (the fallback default) and ``zh_CN`` (the
source Chinese strings). A third pseudo-locale ``"auto"`` resolves at first use to
the system language (Chinese systems -> zh_CN, everything else -> en), so a fresh
install on a non-Chinese machine starts in English with no config.

The module deliberately keeps no Qt dependency: it must import and unit-test
headlessly (the suite runs against ``FakeSipBackend`` with no display). Widgets
subscribe to language changes with ``register_on_change`` and re-bind their
static text in a ``retranslate()`` method.
"""

from __future__ import annotations

import json
import locale
import os
import re
import sys
from pathlib import Path
from typing import Callable

_FALLBACK = "en"
_DEFAULT_LANGUAGE = "auto"
# Locale ids are used verbatim to build a filename ("<lang>.json"); restrict them to
# a safe character class so a tampered config value ("../../etc/passwd") can never
# escape the locales directory. New locales just need their own locales/<lang>.json.
_ALLOWED_LOCALE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_translations: dict[str, dict[str, str]] = {}
_current_language = _DEFAULT_LANGUAGE
_resolved_language: str | None = None
_notified_language: str | None = None
_listeners: list[Callable[[str], None]] = []


def _locales_dir() -> Path:
    """Locate the locales directory for both source and frozen (PyInstaller) runs.

    At runtime from source the JSON sits next to this module; in a PyInstaller
    bundle the modules live inside the PYZ archive, so the data files are collected
    under ``sys._MEIPASS/teleflow/locales`` (declared in the packaging spec).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "teleflow" / "locales"
    return Path(__file__).parent / "locales"


def _detect_windows_ui_language() -> str:
    """Best-effort Windows UI language via kernel32.

    On Windows ``locale.getlocale()``/``getdefaultlocale()`` are unreliable (often
    ``None``), so the only dependable signal for the *display* language is the
    user's preferred UI language from the OS. Primary language id ``0x04`` is
    Chinese; anything else -> English. Returns ``""`` when it can't be determined.
    """
    try:
        import ctypes

        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
    except Exception:
        return ""
    if (langid & 0x3FF) == 0x04:
        return "zh_CN"
    return ""


def _detect_posix_locale() -> str:
    """POSIX/macOS system language via the C locale (env already tried)."""
    try:
        raw = (locale.getlocale()[0] or "").lower()
    except (ValueError, OSError):
        raw = ""
    if not raw:
        try:
            raw = (locale.getdefaultlocale()[0] or "").lower()
        except (ValueError, OSError, NotImplementedError):
            raw = ""
    return "zh_CN" if raw.startswith("zh") else _FALLBACK


def _detect_system_language() -> str:
    """Resolve the OS language to one of our supported locales.

    Honors ``LANGUAGE``/``LC_ALL``/``LANG`` first, then the Windows UI language
    (kernel32) on Windows, then the C locale on POSIX/macOS. Anything resolving to
    ``zh*`` is Chinese; everything else falls back to English.
    """
    raw = (
        os.environ.get("LANGUAGE")
        or os.environ.get("LC_ALL")
        or os.environ.get("LANG")
        or ""
    ).split(":")[0].split(".")[0].strip().lower()
    if raw:
        return "zh_CN" if raw.startswith("zh") else _FALLBACK
    if sys.platform == "win32":
        win = _detect_windows_ui_language()
        if win:
            return win
    return _detect_posix_locale()


def _resolve(lang: str) -> str:
    if lang == "auto":
        return _detect_system_language()
    return lang if _ALLOWED_LOCALE_RE.match(lang) else _FALLBACK


def _load(lang: str) -> dict[str, str]:
    table = _translations.get(lang)
    if table is not None:
        return table
    path = _locales_dir() / f"{lang}.json"
    data: dict[str, str] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    _translations[lang] = data
    return data


def get_language() -> str:
    """Return the currently selected language (may be ``"auto"``)."""
    return _current_language


def set_language(lang: str) -> None:
    """Select the active language and notify subscribers on an actual change.

    ``lang`` may be ``"en"``, ``"zh_CN"``, ``"auto"``, or any safe locale id that
    has a ``locales/<lang>.json`` file. Listeners receive the *resolved* language
    so they can re-bind text without re-detecting; a redundant selection (same
    resolved language as before) fires no notification, so re-created dialogs don't
    waste a retranslate.
    """
    global _current_language, _resolved_language, _notified_language
    _current_language = lang
    resolved = _resolve(lang)
    _resolved_language = resolved
    _load(resolved)  # warm the cache
    if resolved != _notified_language:
        _notified_language = resolved
        for cb in list(_listeners):
            cb(resolved)


def register_on_change(cb: Callable[[str], None]) -> None:
    """Register a callback invoked with the resolved language on a real switch."""
    _listeners.append(cb)


def unregister_on_change(cb: Callable[[str], None]) -> None:
    """Remove a previously registered callback (e.g. a destroyed widget)."""
    if cb in _listeners:
        _listeners.remove(cb)


def tr(key: str, **kwargs: object) -> str:
    """Translate ``key`` for the active language, with ``{placeholder}`` subs.

    Resolution order: active locale -> en fallback -> the key itself. The active
    language is resolved once and cached. Format errors are ignored so a bad
    placeholder never crashes the UI.
    """
    global _resolved_language
    if _resolved_language is None:
        _resolved_language = _resolve(_current_language)
    text: str | None = _load(_resolved_language).get(key)
    if text is None:
        text = _load(_FALLBACK).get(key)
    if text is None:
        text = key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return text


def reset() -> None:
    """Test helper: drop caches/listeners and restore the default language."""
    global _current_language, _resolved_language, _notified_language
    _translations.clear()
    _listeners.clear()
    _current_language = _DEFAULT_LANGUAGE
    _resolved_language = None
    _notified_language = None
