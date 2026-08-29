"""Offline tests for the example hook scripts (no network / no pjsua2).

Mostly exercises the pure text-building logic so the weather hook can be
verified without hitting Open-Meteo or a running TeleFlow RPC.
"""

from __future__ import annotations

import importlib.util
import pathlib


def _load_weather_hook():
    path = pathlib.Path(__file__).resolve().parent.parent / "examples" / "weather_hook.py"
    spec = importlib.util.spec_from_file_location("weather_hook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_describe_weather_builds_chinese_summary() -> None:
    wh = _load_weather_hook()
    text = wh.describe_weather(
        {"temperature_2m": 18.4, "weather_code": 1, "wind_speed_10m": 3.2}
    )
    assert "宁波" in text
    assert "18" in text  # rounded to 18 度
    assert "大致晴朗" in text  # WMO code 1
    assert "3" in text  # wind


def test_describe_weather_handles_unknown_code() -> None:
    wh = _load_weather_hook()
    text = wh.describe_weather({"temperature_2m": 0.0, "weather_code": 999})
    assert "天气代码 999" in text
