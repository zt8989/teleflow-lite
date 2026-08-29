#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IVR digit-1 hook: query Ningbo weather, announce it, then return to menu.

Invoked by TeleFlow's per-digit IVR hook (``ivr_digit_hook["1"]``) with
``{call_id}``::

    python examples/weather_hook.py --call-id {call_id}

Flow:
  1. Query Ningbo current weather from Open-Meteo (no API key required).
  2. POST ``{"call_id", "text"}`` to ``/v1/play`` so TeleFlow speaks it
     (TTS/ffmpeg happen inside TeleFlow — this script only moves text).
  3. Wait for the prompt to finish (estimated from text length).
  4. POST ``{"call_id"}`` to ``/v1/ivr/replay`` so the 1~9~0 menu is
     re-announced and the caller can press another key.

Reads the RPC token / port from TeleFlow's config automatically. Only stdlib
is required, so it runs under the venv python that has the ``teleflow`` package.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Ningbo (宁波市) coordinates.
NINGBO_LAT = 29.8683
NINGBO_LON = 121.5440

# WMO weather interpretation codes -> short Chinese description.
WMO = {
    0: "晴",
    1: "大致晴朗",
    2: "局部多云",
    3: "阴",
    45: "有雾",
    48: "有雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "阵雪",
    86: "阵雪",
    95: "雷阵雨",
    96: "雷阵雨伴冰雹",
    99: "雷阵雨伴冰雹",
}


def _load_token_and_port() -> tuple[str, str]:
    """Resolve the RPC token and URL base (CLI/env -> TeleFlow config -> empty)."""
    token = os.environ.get("TELEFLOW_RPC_TOKEN", "")
    port = "8731"
    try:
        from teleflow.config import ConfigStore  # local import keeps it usable standalone

        settings = ConfigStore().load()
        token = token or settings.rpc_token
        port = str(settings.rpc_port)
    except Exception:  # pragma: no cover - optional integration only
        pass
    return token, port


def describe_weather(current: dict) -> str:
    """Build the spoken summary from an Open-Meteo ``current`` block."""
    temp = current.get("temperature_2m")
    code = current.get("weather_code")
    wind = current.get("wind_speed_10m")
    desc = WMO.get(code, f"天气代码 {code}")
    parts = [f"宁波当前天气，{desc}"]
    if temp is not None:
        parts.append(f"气温 {temp:.0f} 度")
    if wind is not None:
        parts.append(f"风速 {wind:.0f} 米每秒")
    return "，".join(parts) + "。"


def fetch_weather() -> dict | None:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={NINGBO_LAT}&longitude={NINGBO_LON}"
        "&current=temperature_2m,weather_code,wind_speed_10m"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "teleflow-weather-hook/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - public HTTPS
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("current")
    except (urllib.error.URLError, ValueError) as exc:
        print(f"[weather_hook] 天气获取失败: {exc}", file=sys.stderr)
        return None


def _post(url: str, token: str, payload: dict) -> int:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - loopback only
            result = json.loads(resp.read().decode("utf-8"))
        print(f"[weather_hook] {url} -> {result}")
        return 0
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(f"[weather_hook] RPC 错误 {exc.code}: {detail}", file=sys.stderr)
        return 2
    except urllib.error.URLError as exc:
        print(f"[weather_hook] 无法连接 TeleFlow RPC: {exc.reason}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TeleFlow IVR 天气 hook（宁波）")
    parser.add_argument("--call-id", required=True, help="当前呼入 call_id（由 TeleFlow 注入）")
    parser.add_argument("--url", default=None, help="RPC base URL（默认从配置自动获取）")
    parser.add_argument("--token", default=None, help="Bearer token（默认从配置/环境变量获取）")
    args = parser.parse_args(argv)

    token, port = _load_token_and_port()
    token = args.token or token
    base = args.url or os.environ.get("TELEFLOW_RPC_URL") or f"http://127.0.0.1:{port}"
    call_id = args.call_id

    if not token:
        print("[weather_hook] 未配置 RPC token，无法鉴权", file=sys.stderr)
        return 2

    current = fetch_weather()
    if current is None:
        text = "暂时无法获取宁波天气，请稍后再试。"
    else:
        text = describe_weather(current)

    # 1) speak the weather into the live call
    if _post(f"{base}/v1/play", token, {"call_id": call_id, "text": text}) != 0:
        return 2

    # 2) wait for the prompt to finish, then return the caller to the menu
    #    (rough estimate; TTS pacing varies, so pad a little).
    time.sleep(max(1.5, len(text) * 0.3))
    return _post(f"{base}/v1/ivr/replay", token, {"call_id": call_id})


if __name__ == "__main__":
    raise SystemExit(main())
