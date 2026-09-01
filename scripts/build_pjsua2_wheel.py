#!/usr/bin/env python3
"""Build a vendored pjsua2 wheel from the pjsua2 already installed in this project's .venv.

Prereq: pjsua2 must first be built into .venv (see docs/build-pjsua2.md). This script only
packages the existing pjsua2.py + _pjsua2*.so into a wheel that survives `uv sync` / `uv run`
(which would otherwise recreate .venv and drop a hand-built, unmanaged module).

pjsua2 is a *native* extension, so the wheel is inherently platform/Python-specific. pyproject.toml
therefore resolves pjsua2 **by name** from the `dist/` wheel store via `[tool.uv] find-links` —
it contains NO platform-specific filename. uv scans `dist/`, picks the wheel whose tags match
the current host, and the dependency marker (see pyproject.toml) gates installation to that
platform. This script just drops the freshly built wheel into `dist/` (replacing any previous
one) and installs it with `uv sync --extra pjsua2`. The committed pyproject.toml works on every
platform unchanged.

Run it with the project venv python, e.g.:
    .venv/bin/python scripts/build_pjsua2_wheel.py        # macOS / Linux
    .venv/Scripts/python.exe scripts/build_pjsua2_wheel.py # Windows
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # scripts/ -> repo root


def find_venv_python() -> Path:
    """Locate the venv interpreter cross-platform."""
    if sys.platform == "win32":
        cand = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        cand = ROOT / ".venv" / "bin" / "python"
    if not cand.is_file():
        sys.exit(f"error: venv python not found at {cand}. Create it (uv venv) first.")
    return cand


def venv_query(venv_py: Path, code: str) -> str:
    """Run a snippet in the venv python and return its stdout (stripped)."""
    res = subprocess.run(
        [str(venv_py), "-c", code], check=True, capture_output=True, text=True
    )
    return res.stdout.strip()


def detect_tag(venv_py: Path):
    """Return (pytag, sys_platform, marker_machine, pyver, wheel_tag)."""
    pytag = venv_query(
        venv_py, "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')"
    )
    sys_platform = venv_query(venv_py, "import sys; print(sys.platform)")
    machine = venv_query(venv_py, "import platform; print(platform.machine())")
    pyver = venv_query(
        venv_py,
        "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
    )

    m = machine.lower()
    if sys_platform == "darwin":
        # macOS 11 (Big Sur) is the baseline that still supports arm64 slices.
        plat = f"macosx_11_0_{m}"
    elif sys_platform == "linux":
        # manylinux_2_17 covers the common glibc-based distros.
        if m in ("x86_64", "amd64"):
            plat = "manylinux_2_17_x86_64"
        elif m in ("aarch64", "arm64"):
            plat = "manylinux_2_17_aarch64"
        else:
            plat = f"manylinux_2_17_{m}"
    elif sys_platform == "win32":
        # Wheel platform tag is lowercased (win_amd64); the PEP 508 marker keeps the
        # real `platform_machine` casing (e.g. AMD64) so `python_version`/marker match.
        plat = f"win_{m.lower()}"
    else:
        plat = f"{sys_platform}_{m}"

    wheel_tag = f"{pytag}-{pytag}-{plat}"
    return pytag, sys_platform, machine, pyver, wheel_tag


def main() -> None:
    # Make the wheel reproducible: `wheel pack` embeds file timestamps in the zip, which would
    # otherwise change the wheel's hash on every rebuild and desync it from uv.lock. Pinning
    # SOURCE_DATE_EPOCH yields a deterministic wheel (given identical inputs).
    os.environ["SOURCE_DATE_EPOCH"] = "0"

    venv_py = find_venv_python()

    # `wheel` is provided by the dev dependency-group (installed via `uv sync`). The uv-managed
    # venv has no `pip`, so we pack with the venv's `wheel` directly.
    try:
        venv_query(venv_py, "import wheel")
    except subprocess.CalledProcessError:
        sys.exit(
            "error: the 'wheel' package is not in .venv. Run 'uv sync' first (it installs "
            "'wheel' via the dev group), then re-run this script."
        )

    # Use sysconfig (not site.getsitepackages()[0]): in a uv-managed venv the latter can
    # list the venv root as element 0, so it is not reliably the site-packages directory.
    sp = Path(venv_query(venv_py, "import sysconfig; print(sysconfig.get_path('purelib'))"))
    py_mod = sp / "pjsua2.py"
    so_mods = list(sp.glob("_pjsua2*.so")) + list(sp.glob("_pjsua2*.pyd"))
    if not py_mod.is_file() or not so_mods:
        sys.exit(
            f"error: pjsua2 not found in .venv site-packages ({sp}).\n"
            "       Build it first — see docs/build-pjsua2.md."
        )
    so_mod = so_mods[0]

    _pytag, sys_platform, machine, pyver, wheel_tag = detect_tag(venv_py)
    wheel_name = f"pjsua2-2.17-{wheel_tag}.whl"

    out_dir = ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    # Drop any previously built pjsua2 wheel so only the current platform's wheel remains.
    for old in out_dir.glob("pjsua2-2.17-*.whl"):
        old.unlink()

    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "pjsua2-2.17"
        pkg.mkdir()
        shutil.copy(py_mod, pkg / "pjsua2.py")
        shutil.copy(so_mod, pkg / so_mod.name)
        di = pkg / "pjsua2-2.17.dist-info"
        di.mkdir()
        (di / "METADATA").write_text(
            "Metadata-Version: 2.1\n"
            "Name: pjsua2\n"
            "Version: 2.17\n"
            "Summary: Vendored pjsua2 native module (pjproject 2.17) for TeleFlow.\n"
        )
        (di / "WHEEL").write_text(
            "Wheel-Version: 1.0\n"
            "Generator: manual (teleflow scripts/build_pjsua2_wheel.py)\n"
            "Root-Is-Purelib: false\n"
            f"Tag: {wheel_tag}\n"
        )
        subprocess.run(
            [str(venv_py), "-m", "wheel", "pack", str(pkg), "-d", str(out_dir)],
            check=True,
        )

    wheel_path = out_dir / wheel_name
    print(f"Built wheel: {wheel_path}")
    # Install the freshly built wheel via uv so it is locked and `uv run` sees it. uv resolves
    # pjsua2 by name from `dist/` ([tool.uv] find-links), so no pyproject.toml edit is needed.
    try:
        subprocess.run(["uv", "sync", "--extra", "pjsua2"], check=True, cwd=ROOT)
        print("Installed pjsua2 into the venv via `uv sync --extra pjsua2`.")
    except subprocess.CalledProcessError:
        print(
            "Wheel built, but `uv sync --extra pjsua2` failed. Run it manually to install pjsua2."
        )


if __name__ == "__main__":
    main()