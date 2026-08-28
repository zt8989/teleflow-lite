#!/usr/bin/env bash
#
# Build the TeleFlow macOS DMG (ticket 07).
#
# Usage:  ./packaging/macos/build_dmg.sh
#
# Freezes the app with PyInstaller into an onedir TeleFlow.app, then wraps it
# in a compressed DMG. The app is left UNSIGNED: it launches fine for the
# building user (no quarantine, no Gatekeeper block), but distributing to other
# machines requires an Apple Developer signature + notarization, which is out of
# scope here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

VENV_PY="$ROOT/.venv/bin/python"
SPEC="packaging/macos/teleflow.spec"
OUTDIR="packaging/macos/dist"
BUILD="packaging/macos/build"
APP="$OUTDIR/TeleFlow.app"
DMG="packaging/macos/TeleFlow-macos.dmg"
DMGROOT="packaging/macos/dmgroot"

# 1. Freeze the app.
"$VENV_PY" -m PyInstaller --clean --noconfirm --distpath "$OUTDIR" --workpath "$BUILD" "$SPEC"

# 2. Drop any quarantine xattr so the bundle launches locally without the
#    "unidentified developer" Gatekeeper prompt.
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

# 3. Build the DMG with a convenience symlink to /Applications.
rm -rf "$DMGROOT" "$DMG"
mkdir -p "$DMGROOT"
cp -R "$APP" "$DMGROOT/"
ln -s /Applications "$DMGROOT/Applications"
hdiutil create -volname "TeleFlow" -srcfolder "$DMGROOT" -ov -format UDZO "$DMG"
rm -rf "$DMGROOT"

echo "Built $DMG"
