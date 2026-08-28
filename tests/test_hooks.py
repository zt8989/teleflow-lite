"""Tests for the SIP call-lifecycle hook mechanism (ticket 01 / 02)."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from teleflow.config import ConfigStore
from teleflow.hooks import SubprocessHookRunner, attach_hooks
from teleflow.sip import FakeSipBackend, SipCoreService


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


def test_on_hook_fires_on_call_ended(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "c.json")
    settings = store.load()
    settings.on_hook_cmd = "on-hangup.sh {call_id}"
    store.save(settings)

    backend = FakeSipBackend()
    service = SipCoreService(backend, store)
    recorder = _RecordingHookRunner()
    attach_hooks(service, recorder, store)

    service.start()
    backend.receive_invite("C1")
    backend.receive_bye("C1")  # landline hangs up

    assert ("on-hangup.sh {call_id}", {"call_id": "C1"}) in recorder.calls


def test_on_hook_fires_only_after_call_ends(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "c.json")
    settings = store.load()
    settings.on_hook_cmd = "on-hangup.sh {call_id}"
    store.save(settings)

    backend = FakeSipBackend()
    service = SipCoreService(backend, store)
    recorder = _RecordingHookRunner()
    attach_hooks(service, recorder, store)

    service.start()
    backend.receive_invite("C1")

    # Before the call ends, no on-hook command has run.
    assert all(c[0] != "on-hangup.sh {call_id}" for c in recorder.calls)

    backend.receive_bye("C1")
    assert ("on-hangup.sh {call_id}", {"call_id": "C1"}) in recorder.calls
