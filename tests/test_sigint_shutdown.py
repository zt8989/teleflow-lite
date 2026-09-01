"""Regression test for the Ctrl+C / SIGINT shutdown crash.

Running ``python -m teleflow.app`` and pressing Ctrl+C used to crash (segfault
while PyQt cleared the KeyboardInterrupt raised inside the Qt event loop); later
fixes made the app silently ignore Ctrl+C in a real terminal ("no response")
because the signal could land on a pjsua2 worker thread where Python's
main-thread-only signal handling drops it. The fix blocks SIGINT/SIGTERM in
every thread and consumes them via a dedicated ``sigwait`` waiter that posts the
quit to the Qt loop.

This test reproduces the *real terminal* path: the app runs attached to a
pseudo-terminal and we send the INTR byte (``b'\\x03'``) that a terminal's
Ctrl+C generates, then assert a clean exit (return code 0). Sending
``proc.send_signal`` directly would not exercise this path — it bypasses the
pty line discipline entirely.

Runs only when PyQt6 and pty are importable.
"""

import json
import os
import select
import signal
import socket
import sys
import tempfile
import time
from pathlib import Path

import pytest

try:
    import PyQt6  # noqa: F401

    _HAVE_GUI = True
except Exception:  # pragma: no cover - environment dependent
    _HAVE_GUI = False

try:
    import pty  # noqa: F401  (import fails on Windows: termios is Unix-only)

    _HAVE_PTY = True
except (ImportError, OSError):  # pragma: no cover - environment dependent
    _HAVE_PTY = False

pytestmark = pytest.mark.skipif(
    not (_HAVE_GUI and _HAVE_PTY),
    reason="PyQt6 or pty not available",
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _drain(master: int, out: bytearray) -> None:
    """Read whatever the child has written to the pty (bounded, non-blocking)."""
    while select.select([master], [], [], 0)[0]:
        try:
            chunk = os.read(master, 65536)
        except OSError:
            return
        if not chunk:
            return
        out += chunk


@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded, use of forkpty.*:DeprecationWarning"
)
def test_sigint_quits_cleanly() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    port = _free_port()
    # Drive build_app() directly with a temp config on a free port so we don't
    # collide with any already-running instance (or its default RPC port).
    script = (
        "import os, tempfile\n"
        "from pathlib import Path\n"
        "tmp = Path(tempfile.mkdtemp())\n"
        f"cfg = tmp / 'config.json'\n"
        f"cfg.write_text({json.dumps({'rpc_port': port, 'start_minimized': True})!r})\n"
        "from teleflow.app import build_app\n"
        "build_app(config_path=cfg).exec()\n"
    )

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(repo_root / "src") + os.pathsep + env.get("PYTHONPATH", "")

    pid, master = pty.fork()
    if pid == 0:
        os.execvp(sys.executable, [sys.executable, "-c", script])
        os._exit(127)  # pragma: no cover - unreachable

    out = bytearray()
    try:
        # Let the app start (offscreen Qt + build_app takes a couple of seconds);
        # bail out if it dies during startup.
        start = time.monotonic()
        while time.monotonic() - start < 5.0:
            _drain(master, out)
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited != 0:
                pytest.fail(
                    "app exited during startup "
                    f"(code {os.waitstatus_to_exitcode(status)}):\n"
                    f"{out.decode(errors='replace')}"
                )
            time.sleep(0.05)

        # A real terminal turns Ctrl+C into the INTR byte on the pty, which the
        # line discipline converts into SIGINT for the foreground process group.
        try:
            os.write(master, b"\x03")
        except OSError:  # pragma: no cover - child died right before the write
            pytest.fail(f"pty closed before Ctrl+C could be sent:\n{out.decode(errors='replace')}")

        deadline = time.monotonic() + 10
        exited = None
        while time.monotonic() < deadline:
            _drain(master, out)
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited != 0:
                exited = status
                break
            time.sleep(0.05)
        if exited is None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            pytest.fail(
                "app did not exit after Ctrl+C (hung):\n" f"{out.decode(errors='replace')}"
            )

        rc = os.waitstatus_to_exitcode(exited)
        assert rc == 0, (
            f"app exited with code {rc} after Ctrl+C (expected clean exit 0):\n"
            f"{out.decode(errors='replace')}"
        )
    finally:
        os.close(master)