"""Tests for the pure-Python i18n core (ticket 01).

The module must work headlessly with no Qt, so we only exercise tr/set_language/
register_on_change and the "auto" resolution. Locale JSON is seeded minimally;
these tests do not depend on the full UI catalog.
"""

from __future__ import annotations

import locale
import sys

import pytest

from teleflow import i18n


@pytest.fixture
def fresh_i18n(monkeypatch):
    i18n.reset()
    # Force a deterministic non-Chinese system so "auto" is English by default,
    # regardless of the host OS UI language (env unset + Windows/macOS UI lang
    # forced to non-Chinese so the POSIX fallback resolves to English).
    monkeypatch.setattr(locale, "getdefaultlocale", lambda: ("en_US", "UTF-8"))
    # _detect_posix_locale() consults locale.getlocale() *before* getdefaultlocale,
    # and on a Chinese-region host getlocale() reflects that region (zh_CN). Pin it
    # too so "auto" resolves to English regardless of the host's actual locale.
    monkeypatch.setattr(locale, "getlocale", lambda: ("en_US", "UTF-8"))
    monkeypatch.setattr(i18n, "_detect_windows_ui_language", lambda: "en")
    monkeypatch.setattr(i18n, "_detect_darwin_language", lambda: "")
    for var in ("LANGUAGE", "LC_ALL", "LANG"):
        monkeypatch.delenv(var, raising=False)
    yield
    i18n.reset()


def test_tr_returns_english_fallback_by_default(fresh_i18n):
    assert i18n.tr("language.english") == "English"


def test_set_language_switches_lookup(fresh_i18n):
    i18n.set_language("zh_CN")
    assert i18n.tr("language.chinese") == "中文"
    assert i18n.tr("language.english") == "English"


def test_unknown_key_falls_back_to_key(fresh_i18n):
    assert i18n.tr("no.such.key") == "no.such.key"


def test_fallback_chain_zh_to_en_to_key(fresh_i18n):
    # "app.name" exists only in en.json, so zh_CN must fall through to en.
    i18n.set_language("zh_CN")
    assert i18n.tr("app.name") == "TeleFlow"


def test_placeholder_substitution(fresh_i18n):
    # "greet" lives only in en.json; exercise {placeholder} substitution via fallback.
    assert i18n.tr("greet", name="World") == "Hello World"


def test_bad_placeholder_does_not_crash(fresh_i18n):
    assert i18n.tr("greet") == "Hello {name}"  # format error swallowed


def test_register_on_change_fires_with_resolved_lang(fresh_i18n):
    seen = []
    i18n.register_on_change(seen.append)
    i18n.set_language("zh_CN")
    assert seen == ["zh_CN"]
    i18n.set_language("en")
    assert seen == ["zh_CN", "en"]


def test_auto_resolves_to_english_on_non_chinese_system(fresh_i18n):
    i18n.set_language("auto")
    assert i18n.get_language() == "auto"
    assert i18n.tr("language.english") == "English"


def test_auto_resolves_to_chinese_when_lang_is_zh(fresh_i18n, monkeypatch):
    # POSIX path: a zh_CN LANG selects Chinese. (Windows ignores LANG for UI
    # language, so this path is only exercised off-win32.)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.setattr(sys, "platform", "linux")
    i18n.set_language("auto")
    assert i18n.tr("language.chinese") == "中文"


def test_auto_resolves_to_chinese_via_windows_ui_language(monkeypatch):
    # On a Chinese Windows host (env unset) the kernel32 UI language wins; the
    # fixture's windows stub is overridden here to prove the path is used.
    i18n.reset()
    for var in ("LANGUAGE", "LC_ALL", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(i18n, "_detect_windows_ui_language", lambda: "zh_CN")
    i18n.set_language("auto")
    assert i18n.tr("language.chinese") == "中文"
    i18n.reset()


def test_detect_darwin_language_reads_apple_preferences(monkeypatch):
    # macOS keeps the UI language in AppleLanguages/AppleLocale, which the shell
    # LANG does not reflect. A Chinese AppleLanguages must win over an en_US LANG.
    class _Run:
        def __init__(self, stdout, rc=0):
            self.stdout = stdout
            self.returncode = rc

    def _fake_run(cmd, **kwargs):
        if cmd[-1] == "AppleLanguages":
            return _Run('(\n    "zh-Hans",\n    en\n)')
        return _Run("en_US")

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert i18n._detect_darwin_language() == "zh_CN"


def test_detect_darwin_language_falls_through_to_posix(monkeypatch):
    # When Apple preferences are unavailable (defaults errors out) the function
    # returns "" so the caller falls back to the POSIX env-var detection.
    def _fake_run(cmd, **kwargs):
        raise OSError("no defaults")

    monkeypatch.setattr("subprocess.run", _fake_run)
    assert i18n._detect_darwin_language() == ""


def test_invalid_language_falls_back_safely(fresh_i18n):
    # A tampered config value must not escape the locales dir or crash.
    i18n.set_language("../../etc/passwd")
    assert i18n.tr("app.name") == "TeleFlow"  # falls back to en, no path traversal


def test_unregister_on_change_stops_notifications(fresh_i18n):
    seen = []
    i18n.register_on_change(seen.append)
    i18n.unregister_on_change(seen.append)
    i18n.set_language("zh_CN")
    assert seen == []


def test_redundant_set_language_does_not_renotify(fresh_i18n):
    seen = []
    i18n.register_on_change(seen.append)
    i18n.set_language("en")
    i18n.set_language("en")  # same resolved language -> no second notification
    assert seen == ["en"]
