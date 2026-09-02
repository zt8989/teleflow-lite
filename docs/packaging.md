# 打包

TeleFlow 通过 PyInstaller 冻结为独立可执行文件。macOS 产出 `.app` 再封装 DMG，Windows 产出 onedir 目录含 `.exe`。

## 依赖

依赖用 **uv** 管理：一条 `uv sync` 即可装好运行时依赖（`PyQt6`）、vendored 的 `pjsua2`
wheel，以及开发工具；再用 uv 把 `pyinstaller` 装进同一个 `.venv`：

```bash
uv sync
uv pip install pyinstaller
```

打包时要求项目 `.venv` 中已装好 `pjsua2`（来自 vendored wheel，随 `uv sync` 自动安装）、
`PyQt6` 与 `pyinstaller`。

> **ffmpeg**：phone-report 功能需要 ffmpeg 将合成的 MP3 转码为 8 kHz mono WAV。
> ffmpeg **不随包分发**，需在目标机器上安装（macOS: `brew install ffmpeg`；
> Windows: `winget install Gyan.FFmpeg` 或从 https://ffmpeg.org 下载），
> 放到 PATH 上或通过 TeleFlow 设置里的 `ffmpeg_path` 指定。

---

## 构建 macOS DMG

```bash
./packaging/macos/build_dmg.sh
```

产物：

- `packaging/macos/dist/TeleFlow.app` —— 可独立运行的 App（onedir）。
- `packaging/macos/TeleFlow-macos.dmg` —— 分发包，内含 `TeleFlow.app` 与一个指向
  `/Applications` 的快捷方式，方便拖拽安装。

脚本做了三件事：

1. 调用 `PyInstaller` 按 `packaging/macos/teleflow.spec` 冻结；
2. 去掉构建产物上的 `com.apple.quarantine` 隔离属性，使本机双击即可运行；
3. 用 `hdiutil` 生成压缩 DMG。

### macOS spec 关键配置

- `hiddenimports=["pjsua2"]`：pjsua2 是原生扩展，且只在函数体内 `import`，
  静态分析抓不到，必须显式声明，否则冻结后运行会报 `ModuleNotFoundError: pjsua2`。
- `info_plist.NSMicrophoneUsageDescription`：TeleFlow 读取的是音频*输入*设备
  （如 BlackHole 虚拟声卡），macOS 的 TCC 把它当作麦克风权限，因此必须声明用途
  文案，否则捕获侧会被拒绝。
- `console=False`：窗口应用，无终端。

### macOS 已知限制

- **未签名**：当前 DMG 未做 Apple 开发者签名。本机构建的 App 因不带 quarantine
  属性可直接运行；但若拷贝到其它 Mac，Gatekeeper 会拦截（右键 → 打开，或
  `xattr -dr com.apple.quarantine TeleFlow.app` 可绕过）。如需分发给他人，需
  `codesign` 签名 + Apple notarization。
- **架构**：本机构建为 **arm64（Apple Silicon）**。要支持 Intel Mac，需在一台
  universal Python（或 Intel 机器）上重跑本脚本；PyInstaller 的 `target_arch`
  也可设为 `universal2`（需对应 Python 支持）。
- **音频权限**：首次从虚拟声卡（BlackHole）采集时，macOS 会弹麦克风授权框，
  允许即可；这与 PRD 中"macOS 正确请求音频权限且不崩溃"的要求一致。

---

## 构建 Windows 安装包

```powershell
.\packaging\windows\build.ps1
```

产物：

- `TeleFlow-windows-0.1.0-setup.exe` —— NSIS 安装包（~30 MB），支持两种安装范围。
- `packaging\windows\dist\TeleFlow\` —— 冻结后的 onedir 目录（供调试用）。

脚本分两步：

1. 调用 `PyInstaller` 按 `packaging\windows\teleflow.spec` 冻结；
2. 调用 `NSIS`（`packaging\windows\installer.nsi`）生成安装包。

### 安装范围

安装时可选择：

- **为所有用户安装**：安装到 `Program Files\TeleFlow`，注册表写入 HKLM，
  需要管理员权限。
- **仅为当前用户安装**：安装到 `%LOCALAPPDATA%\TeleFlow`，注册表写入 HKCU，
  无需管理员权限。

### NSIS 依赖

构建安装包需要 [NSIS](https://nsis.sourceforge.io/)：

```powershell
winget install NSIS.NSIS
```

### Windows spec 关键配置

- `hiddenimports=["pjsua2"]`：与 macOS 相同，pjsua2 延迟导入需显式声明。
- `console=False`：窗口应用，无终端。
- `icon=TeleFlow.ico`：应用图标，从 macOS 的 `.icns` 转换而来。

### Windows 已知限制

- **未签名**：EXE 未做代码签名。在本机可直接运行；分发到其它机器可能触发
  Windows SmartScreen 警告（"Windows 已保护你的电脑"）。如需广泛分发，
  需要代码签名证书。
- **Qt6Gui.dll 崩溃**：已知在 Windows 上，若 pjsua2 栈仍在运行时关闭窗口，
  可能触发 Qt6Gui.dll 访问违规（0xC0000005）。`app.py` 中的 `quit_app`
  已做防护（先调用 `_cleanup()` 再 `QApplication.quit()`）。
- **WMME 设备名**：中文 Windows 上 WMME 设备名可能为 UTF-8 代理对转义的 GBK
  字节，`audio.py` 中的 `_recover_wmme_names` 已处理此情况。
