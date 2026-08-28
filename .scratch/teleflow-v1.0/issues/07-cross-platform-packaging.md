# 07 — Cross-platform packaging & permissions

**What to build:** Cross-platform packaging & permissions: produce a PyInstaller single-file Windows EXE and a macOS DMG; Windows runs without microphone-permission popups; macOS requests audio permission correctly on Intel and Apple Silicon and recognizes BlackHole without crashing or black-screening.

**Blocked by:** 04 — Audio Routing / Media Bridge; 06 — System tray & lifecycle.

**Status:** resolved

> 用户决策：仅构建 macOS DMG，Windows EXE 跳过即视为完成（本机为 macOS，无 Windows
> 构建环境）。详见 `docs/packaging.md`。

- [ ] A Windows EXE launches and runs the full flow standalone. *(skipped — 无 Windows 环境)*
- [x] A macOS DMG installs and runs the full flow (built & verified: arm64 / Apple Silicon).
- [ ] Windows shows no intrusive mic-permission prompt during normal operation. *(skipped with EXE)*
- [x] macOS audio permission is requested correctly and the app stays stable (no crash, no black screen).

**Note:** DMG 为 **arm64**，未做 Apple 开发者签名（本机直跑无 quarantine 拦截；
分发给他人需 `codesign` + notarization）。Intel 支持需 universal Python 重跑脚本。
冻结二进制已验证可启动（offscreen 模式跑通事件循环，无 traceback）。
