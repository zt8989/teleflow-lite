# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the TeleFlow macOS app (ticket 07).

Produces an onedir ``TeleFlow.app`` bundle. The pjsua2 native extension is
imported lazily inside functions, so it must be named explicitly as a
``hiddenimport`` or it would be dropped from the frozen binary. PyQt6 is handled
by PyInstaller's own hook.
"""

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT, BUNDLE

import os

# PyInstaller ``exec``s this spec without ``__file__``. ``DISTPATH`` is provided
# by PyInstaller as an absolute path (packaging/macos/dist), so its parent is
# this directory and two levels up is the repo root.
_HERE = os.path.dirname(DISTPATH)
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
ENTRY = os.path.join(_HERE, "entry.py")
SRC = os.path.join(_REPO, "src")

block_cipher = None

ICON = os.path.join(_HERE, "TeleFlow.icns")

a = Analysis(
    [ENTRY],
    pathex=[SRC],
    binaries=[],
    datas=[
        (os.path.join(_REPO, "prototypes", "teleflow-icon.svg"), "prototypes"),
        (os.path.join(_REPO, "prototypes", "teleflow-icon-mono.svg"), "prototypes"),
        # i18n locale catalogs: collected next to the frozen modules
        # (src/teleflow/locales -> teleflow/locales) so i18n._locales_dir finds them.
        (os.path.join(_REPO, "src", "teleflow", "locales"), "teleflow/locales"),
    ],
    hiddenimports=["pjsua2"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TeleFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TeleFlow",
)

app = BUNDLE(
    coll,
    name="TeleFlow.app",
    icon=ICON,
    bundle_identifier="com.teleflow.lite",
    info_plist={
        # TeleFlow reads an audio *input* device (e.g. BlackHole) even though it
        # is a virtual sound card; macOS TCC treats that as microphone access,
        # so we must declare the usage string or the capture side will be denied.
        "NSMicrophoneUsageDescription": (
            "TeleFlow 需要访问音频输入设备（如 BlackHole 虚拟声卡），"
            "以便把座机通话的声音流转到您的电脑。"
        ),
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.15",
    },
)
