# 打包（macOS DMG）

TeleFlow 通过 PyInstaller 冻结为独立的 `TeleFlow.app`，再封装进 DMG。
Windows EXE 暂不构建（本机为 macOS，无 Windows 构建环境）。

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

## 关键配置（spec）

- `hiddenimports=["pjsua2"]`：pjsua2 是原生扩展，且只在函数体内 `import`，
  静态分析抓不到，必须显式声明，否则冻结后运行会报 `ModuleNotFoundError: pjsua2`。
- `info_plist.NSMicrophoneUsageDescription`：TeleFlow 读取的是音频*输入*设备
  （如 BlackHole 虚拟声卡），macOS 的 TCC 把它当作麦克风权限，因此必须声明用途
  文案，否则捕获侧会被拒绝。
- `console=False`：窗口应用，无终端。

## 已知限制 / 发布前注意

- **未签名**：当前 DMG 未做 Apple 开发者签名。本机构建的 App 因不带 quarantine
  属性可直接运行；但若拷贝到其它 Mac，Gatekeeper 会拦截（右键 → 打开，或
  `xattr -dr com.apple.quarantine TeleFlow.app` 可绕过）。如需分发给他人，需
  `codesign` 签名 + Apple notarization。
- **架构**：本机构建为 **arm64（Apple Silicon）**。要支持 Intel Mac，需在一台
  universal Python（或 Intel 机器）上重跑本脚本；PyInstaller 的 `target_arch`
  也可设为 `universal2`（需对应 Python 支持）。
- **音频权限**：首次从虚拟声卡（BlackHole）采集时，macOS 会弹麦克风授权框，
  允许即可；这与 PRD 中“macOS 正确请求音频权限且不崩溃”的要求一致。

## 依赖

- 依赖用 **uv** 管理：一条 `uv sync` 即可装好运行时依赖（`PyQt6`）、vendored 的 `pjsua2`
  wheel，以及开发工具；再用 uv 把 `pyinstaller` 装进同一个 `.venv`：

  ```bash
  uv sync
  uv pip install pyinstaller
  ```

- 打包时要求项目 `.venv` 中已装好 `pjsua2`（来自 vendored wheel，随 `uv sync` 自动安装）、
  `PyQt6` 与 `pyinstaller`。
