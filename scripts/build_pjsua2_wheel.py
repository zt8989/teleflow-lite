#!/usr/bin/env python3
"""Build a vendored pjsua2 wheel from the pjsua2 already installed in this project's .venv.

Prereq: pjsua2 must first be built into .venv (see docs/build-pjsua2.md). This script only
packages the existing pjsua2.py + _pjsua2*.so into a wheel declared via [tool.uv.sources] so
it survives `uv sync` / `uv run` (which would otherwise recreate .venv and drop a hand-built,
unmanaged module).

pjsua2 is a *native* extension, so the wheel is inherently platform/Python-specific. This
script therefore auto-detects the running platform and Python and bakes the correct wheel tag
in, then rewrites the [tool.uv.sources] path (and the dependency marker) in pyproject.toml to
point at the freshly built wheel. That keeps pyproject.toml correct on every platform without a
hand-edit: build pjsua2, run this script, and `uv lock` picks up the new local wheel.

Run it with the project venv python, e.g.:
    .venv/bin/python scripts/build_pjsua2_wheel.py        # macOS / Linux
    .venv/Scripts/python.exe scripts/build_pjsua2_wheel.py # Windows
"""
from __future__ import annotations

import os
import re
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
        # Wheel tag is lowercased; the marker value keeps the real casing (e.g. AMD64).
        plat = f"win_{m}"
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

    sp = Path(venv_query(venv_py, "import site; print(site.getsitepackages()[0])"))
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

    # Point pyproject.toml's [tool.uv.sources] + dependency marker at the new wheel.
    pyproject = ROOT / "pyproject.toml"
    text = pyproject.read_text()
    path_pat = re.compile(r'(pjsua2 = \{ path = ")[^"]+(" \})')
    if not path_pat.search(text):
        sys.exit(
            "error: could not find the [tool.uv.sources] pjsua2 path line in pyproject.toml"
        )
    marker = (
        f"sys_platform == '{sys_platform}' and platform_machine == '{machine}' "
        f"and python_version == '{pyver}'"
    )
    new_path = f"dist/{wheel_name}"
    new_text = path_pat.sub(rf"\1{new_path}\2", text)
    dep_pat = re.compile(r'("pjsua2 ; )[^"]+(",)')
    new_text = dep_pat.sub(lambda m: f"{m.group(1)}{marker}{m.group(2)}", new_text)
    if new_text != text:
        pyproject.write_text(new_text)
        print(f"updated pyproject.toml: path -> {new_path}; marker -> {marker}")
    else:
        print("pyproject.toml already matches this platform (unchanged)")

    print(f"Built wheel: {wheel_path}")
    print("Next: run 'uv lock' to refresh uv.lock, then 'uv sync'.")


if __name__ == "__main__":
    main()
