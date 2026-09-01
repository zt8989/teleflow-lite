"""Headless CLI for TeleFlow's SIP core (auto-answer + IVR + hooks + report).

Runs the exact same service/backend/TTS stack as the GUI app but with **no
Qt/GUI at all** — the point is to isolate whether the GUI host (PyQt event
loop, log panel, GIL contention) is what makes outbound RTP audio choppy on
macOS. Behavior mirrors the app: register to the configured gateway, auto-answer
inbound calls, announce the configured IVR menu, fire user hook commands, and
optionally serve the RPC control channel.

The GUI app and this CLI must NOT run at the same time (both register the same
SIP account/AOR to the gateway).

Usage:
    uv run python -m teleflow.cli                 # 默认连接网关并注册
    uv run python -m teleflow.cli --no-connect    # 不启动 SIP（仅验证环境）
    uv run python -m teleflow.cli --report "今天预约了客户" --target 1003   # 拨自定义电话播报
"""

from __future__ import annotations

import argparse
import queue
import signal
import sys
import threading
from typing import Callable, cast

from teleflow.config import ConfigStore
from teleflow.hooks import SubprocessHookRunner, attach_hooks
from teleflow.pjsua2_backend import Pjsua2Backend
from teleflow.sip import (
    EVENT_CALL_CONNECTED,
    EVENT_CALL_ENDED,
    EVENT_CALL_INCOMING,
    EVENT_IVR_DIGIT,
    EVENT_MEDIA_ERROR,
    EVENT_REPORT_COMPLETED,
    EVENT_REPORT_FAILED,
    EVENT_REPORT_STARTED,
    EVENT_SIP_PORT_CONFLICT,
    EVENT_SIP_REGISTER_FAILED,
    EVENT_SIP_REGISTERED,
    EVENT_SIP_STARTED,
    EVENT_SIP_UNREGISTERED,
    SipCoreService,
)
from teleflow.tts import CachingTtsBackend, EdgeTtsBackend


def _log(message: str) -> None:
    print(message, flush=True)


class _DeferQueue:
    """``service._defer`` substitute: functors are queued and drained by the
    MAIN thread (which called pjsua2's libCreate/libInit, so it is the one
    thread registered with pjlib — running pjsua2 calls on any other thread
    trips pjsua2's "unknown/external thread" assertion). The GUI app instead
    marshals onto the Qt main thread, which is the same thing."""

    def __init__(self) -> None:
        self._q: queue.Queue[Callable[[], None]] = queue.Queue()

    def __call__(self, fn: Callable[[], None]) -> None:
        self._q.put(fn)

    def drain(self) -> None:
        while True:
            try:
                fn = self._q.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a defer failure must not kill the loop
                _log(f"[CLI] defer 任务失败: {exc}")


def _echo_event(service: SipCoreService) -> None:
    """Mirror the important domain events to stdout so a headless test can
    follow the call lifecycle at a glance. The service calls subscribers as
    ``callback(**data)``, so the handler must take kwargs only."""

    def _make(event: str):
        def _on(**data: object) -> None:
            slim = {k: v for k, v in data.items() if k in ("call_id", "digit")}
            _log(f"[EVENT] {event}" + (f" {slim}" if slim else ""))

        return _on

    for event in (
        EVENT_SIP_STARTED,
        EVENT_SIP_REGISTERED,
        EVENT_SIP_UNREGISTERED,
        EVENT_SIP_REGISTER_FAILED,
        EVENT_SIP_PORT_CONFLICT,
        EVENT_CALL_INCOMING,
        EVENT_CALL_CONNECTED,
        EVENT_CALL_ENDED,
        EVENT_IVR_DIGIT,
        EVENT_REPORT_STARTED,
        EVENT_REPORT_COMPLETED,
        EVENT_REPORT_FAILED,
        EVENT_MEDIA_ERROR,
    ):
        service.on(event, _make(event))


def _resolve_target(target: str | None, settings) -> str | None:
    """Turn ``--target`` into a dialable SIP URI for the report.

    A full SIP URI (contains ``sip:``/``@``) is used as-is; a bare extension
    or phone number is dialed through the configured gateway, mirroring
    ``resolve_report_target`` (sip.py).
    """
    if not target or not str(target).strip():
        return None
    value = str(target).strip()
    if "@" in value or value.lower().startswith("sip:"):
        return value
    host = settings.sip_host.strip()
    if not host:
        return None
    port = settings.sip_server_port or 5060
    return f"sip:{value}@{host}:{port}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="teleflow.cli",
        description="Headless TeleFlow SIP core (no GUI): auto-answer + IVR + hooks + RPC.\n默认连接网关并注册；Ctrl+C 退出。",
    )
    ap.add_argument("--no-connect", action="store_true", help="不启动 SIP（默认是连接）")
    ap.add_argument("--no-hooks", action="store_true", help="不执行配置里的 hook 命令（摘机/挂机/按键）")
    ap.add_argument("--no-rpc", action="store_true", help="不启动本地 RPC 控制端口")
    ap.add_argument("--report", metavar="TEXT", help="注册成功后立即发起一次播报（走报告流程，TTS 合成）")
    ap.add_argument(
        "--target",
        metavar="NUMBER_OR_URI",
        help="--report 的拨号目标：分机号（如 1003）走网关，或完整 SIP URI（如 sip:1003@host:5060）",
    )
    args = ap.parse_args(argv)

    store = ConfigStore()
    settings = store.load()

    tts = CachingTtsBackend(
        EdgeTtsBackend(
            ffmpeg_path=settings.ffmpeg_path,
            retry_attempts=settings.tts_retry_attempts,
            logger=_log,
        ),
        logger=_log,
        cache_ttl_seconds=settings.tts_cache_ttl_seconds,
    )

    try:
        backend = Pjsua2Backend(store)
    except RuntimeError as exc:
        _log(f"[CLI] pjsua2 不可用，无法启动真实后端: {exc}")
        return 1

    service = SipCoreService(backend, store, tts=tts)
    # Documented app-shell wiring (the GUI wires these to its Qt thread): the
    # headless CLI routes them to its own worker thread instead.
    service._log = _log  # type: ignore[attr-defined]
    defer = _DeferQueue()
    service._defer = defer  # type: ignore[attr-defined]

    if not args.no_hooks:
        runner = SubprocessHookRunner(store, log=_log)
        attach_hooks(service, runner, store)
        _log("[CLI] hook 命令已启用 (--no-hooks 可关闭)")

    service.set_device_change_callback(lambda: service.reroute_if_connected())
    _echo_event(service)

    # --report 一次性模式：汇报结束后自动退出（Ctrl+C 仍可中断）。
    report_done = threading.Event()
    report_exit_code = {"code": 0}

    def _finish_report(code: int) -> None:
        report_exit_code["code"] = code
        # _on_report_eof 在发出 COMPLETED 事件之后才把挂断入队到 defer，
        # 留 1s 让主循环把挂断执行掉再退出（避免销毁库时通话还挂着）。
        threading.Timer(1.0, report_done.set).start()

    rpc = None
    if settings.rpc_enabled and not args.no_rpc:
        from teleflow.rpc import RpcServer

        try:
            rpc = RpcServer(
                service,
                store,
                log=_log,
                scheduler=cast(Callable[[Callable[[], object]], object], defer),
            )
            rpc.start()
            _log(f"[RPC] 控制端口已启动 (127.0.0.1:{settings.rpc_port})")
        except Exception as exc:  # noqa: BLE001 - RPC is optional
            _log(f"[RPC] 启动失败（忽略）: {exc}")
            rpc = None

    connect = not args.no_connect
    if connect and settings.sip_host.strip() and settings.sip_user.strip():
        try:
            service.start()
        except Exception as exc:  # noqa: BLE001 - surface and exit cleanly
            _log(f"[CLI] SIP 启动失败: {exc}")
            return 1
        _log("[CLI] SIP core 已启动（自动应答 + IVR）。Ctrl+C 退出。")
    else:
        _log(
            "[CLI] 未启动：--no-connect 已指定，或网关账号未配置（sip_host/sip_user）"
        )
        if args.report:
            _log("[CLI] --report 需要 SIP 已启动，跳过播报")

    if args.report and service.running:
        service.on(EVENT_REPORT_COMPLETED, lambda **_d: _finish_report(0))
        service.on(EVENT_REPORT_FAILED, lambda **_d: _finish_report(1))

        def _wait_registered_and_report() -> None:
            deadline: float = 30
            while deadline > 0 and not service.is_registered:
                threading.Event().wait(0.5)
                deadline -= 0.5
            if not service.is_registered:
                _log("[REPORT] 等注册超时，未拨号")
                report_exit_code["code"] = 1
                report_done.set()
                return
            target = _resolve_target(args.target, settings)
            if args.target and not target:
                _log(f"[REPORT] 无法解析 target={args.target!r}（网关未配置），用默认目标")
            # start_report 内部会调 pjsua2 API，必须在主线程（pjlib 注册线程）执行。
            defer(lambda: service.start_report(str(args.report), target=target))  # type: ignore[arg-type]

        threading.Thread(target=_wait_registered_and_report, daemon=True).start()
    elif args.report:
        _log("[CLI] --report 需要 SIP 已启动，跳过播报并退出")
        report_exit_code["code"] = 1
        report_done.set()

    stop = threading.Event()

    def _on_signal(signum: int, _frame: object) -> None:
        _log(f"\n[CLI] 收到信号 {signum}，正在退出…")
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        while not stop.is_set() and not report_done.is_set():
            defer.drain()
            stop.wait(0.2)
    finally:
        _log("[CLI] 清理 pjsua2…")
        defer.drain()  # 把残留的挂断/重置等 defer 任务跑完再销毁库
        if rpc is not None:
            try:
                rpc.stop()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
        try:
            service.stop()  # -> backend.stop() -> libDestroy
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass
        _log("[CLI] 已退出")
    return report_exit_code["code"]


if __name__ == "__main__":
    sys.exit(main())