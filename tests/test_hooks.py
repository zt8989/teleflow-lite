"""Tests for the SIP call-lifecycle hook mechanism (ticket 01 / 02)."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from teleflow.config import ConfigStore
from teleflow.hooks import SubprocessHookRunner, attach_hooks
from teleflow.sip import FakeSipBackend, SipCoreService
from teleflow.tts import FakeTtsBackend


class _RecordingHookRunner:
    """Fake HookRunner (Protocol impl) that records every run() call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def run(self, command: str, context: dict[str, str]) -> None:
        self.calls.append((command, context))


def test_render_substitutes_call_id() -> None:
    assert (
        SubprocessHookRunner._render("echo {call_id}", {"call_id": "C1"})
        == "echo C1"
    )
    # Unknown placeholders are left untouched.
    assert (
        SubprocessHookRunner._render("echo {call_id} {extra}", {"call_id": "C1"})
        == "echo C1 {extra}"
    )


def test_render_substitutes_last_digit_for_on_hook_script() -> None:
    # The on-hook command may pass the last IVR digit to the script so it can
    # decide whether the Vibe Coding session (key 0) actually started.
    assert (
        SubprocessHookRunner._render(
            "stop.sh --last-digit {last_digit}", {"call_id": "C1", "last_digit": "0"}
        )
        == "stop.sh --last-digit 0"
    )
    assert (
        SubprocessHookRunner._render(
            "stop.sh --last-digit {last_digit}", {"call_id": "C2", "last_digit": ""}
        )
        == "stop.sh --last-digit "
    )


def test_run_is_nonblocking_and_renders_command(
    monkeypatch: object, tmp_path: Path
) -> None:
    started = threading.Event()
    recorded: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        recorded["args"] = args
        recorded["kwargs"] = kwargs
        started.set()
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    store = ConfigStore(tmp_path / "c.json")
    logs: list[str] = []
    runner = SubprocessHookRunner(store, log=logs.append)
    runner.run("echo {call_id}", {"call_id": "C1"})

    # The command must actually be executed, with the placeholder rendered and
    # via the shell.
    assert started.wait(timeout=2), "hook command was never executed"
    assert recorded["args"] == ("echo C1",)
    assert recorded["kwargs"].get("shell") is True  # type: ignore[union-attr]
    assert any("[HOOK]" in line for line in logs)


def test_run_swallows_execution_errors(monkeypatch: object, tmp_path: Path) -> None:
    def fake_run(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    store = ConfigStore(tmp_path / "c.json")
    logs: list[str] = []
    runner = SubprocessHookRunner(store, log=logs.append)

    # Must not propagate out of run(); the failure is logged instead.
    runner.run("bad-command", {"call_id": "C1"})
    assert any("[ERROR]" in line for line in logs), "failed hook was not logged"


def test_run_skips_empty_command(monkeypatch: object, tmp_path: Path) -> None:
    called: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a) or None)
    logs: list[str] = []
    runner = SubprocessHookRunner(ConfigStore(tmp_path / "c.json"), log=logs.append)

    runner.run("", {"call_id": "C1"})
    runner.run("   ", {"call_id": "C1"})

    assert called == []
    assert logs == []


def test_execute_survives_non_utf8_output(tmp_path: Path) -> None:
    # A hook command whose stdout contains bytes invalid in UTF-8 (e.g. a tool
    # printing in the system code page on a Chinese Windows) must not crash
    # subprocess's internal reader thread with UnicodeDecodeError. Output is
    # discarded anyway, so the decode must be lossy rather than fatal.
    emitter = tmp_path / "emit_bytes.py"
    emitter.write_text(
        "import sys\nsys.stdout.buffer.write(b'\\xb2')\n",
        encoding="utf-8",
    )
    command = f'{sys.executable} "{emitter}"'

    # The reader-thread exception lands in threading.excepthook, not in
    # SubprocessHookRunner._execute's own try/except, so we capture it there.
    captured: list[type[BaseException]] = []
    original = threading.excepthook

    def _hook(args: threading.ExceptHookArgs) -> None:
        captured.append(args.exc_type)

    threading.excepthook = _hook
    try:
        runner = SubprocessHookRunner(ConfigStore(tmp_path / "c.json"), log=lambda _: None)
        runner._execute(command)  # synchronous: subprocess.run joins its reader thread
    finally:
        threading.excepthook = original

    assert UnicodeDecodeError not in captured


def test_off_hook_fires_on_call_connected(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "c.json")
    settings = store.load()
    settings.off_hook_cmd = "on-answer.sh {call_id}"
    store.save(settings)

    backend = FakeSipBackend()
    service = SipCoreService(backend, store)
    recorder = _RecordingHookRunner()
    attach_hooks(service, recorder, store)

    service.start()
    backend.receive_invite("C1")

    assert recorder.calls == [("on-answer.sh {call_id}", {"call_id": "C1"})]


def test_off_hook_does_nothing_without_configured_command(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "c.json")
    backend = FakeSipBackend()
    service = SipCoreService(backend, store)
    recorder = _RecordingHookRunner()
    attach_hooks(service, recorder, store)

    service.start()
    backend.receive_invite("C1")

    # The hook fires on CONNECTED and forwards the (empty) configured command;
    # the runner itself is what no-ops an empty command (see
    # test_run_skips_empty_command), so the wiring still invokes it.
    assert recorder.calls == [("", {"call_id": "C1"})]


def test_on_hook_fires_on_call_ended_when_last_digit_zero(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "c.json")
    settings = store.load()
    settings.ivr_enabled = True
    settings.on_hook_cmd = "on-hangup.sh {call_id}"
    store.save(settings)

    backend = FakeSipBackend()
    service = SipCoreService(backend, store, tts=FakeTtsBackend())
    recorder = _RecordingHookRunner()
    attach_hooks(service, recorder, store)

    service.start()
    backend.receive_invite("C1")
    backend.receive_dtmf("C1", "0")  # last key "0" (Vibe Coding start)
    backend.receive_bye("C1")  # landline hangs up

    assert ("on-hangup.sh {call_id}", {"call_id": "C1", "last_digit": "0"}) in recorder.calls


def test_on_hook_fires_on_every_hangup(tmp_path: Path) -> None:
    # The IVR call is always bridged two-way, so the on-hook command fires on
    # every hang-up (no exit-digit gate). The last digit pressed is carried in
    # the context.
    store = ConfigStore(tmp_path / "c.json")
    settings = store.load()
    settings.ivr_enabled = True
    settings.on_hook_cmd = "on-hangup.sh {call_id}"
    store.save(settings)

    backend = FakeSipBackend()
    service = SipCoreService(backend, store, tts=FakeTtsBackend())
    recorder = _RecordingHookRunner()
    attach_hooks(service, recorder, store)

    service.start()
    # Last digit "0" -> on-hook still fires (no guard).
    backend.receive_invite("C1")
    backend.receive_dtmf("C1", "0")
    backend.receive_bye("C1")
    assert ("on-hangup.sh {call_id}", {"call_id": "C1", "last_digit": "0"}) in recorder.calls

    # Pressing "7" then hang up -> on-hook fires again.
    backend.receive_invite("C2")
    backend.receive_dtmf("C2", "7")
    backend.receive_bye("C2")
    assert ("on-hangup.sh {call_id}", {"call_id": "C2", "last_digit": "7"}) in recorder.calls
