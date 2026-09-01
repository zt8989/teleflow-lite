# 编译 pjsua2 原生模块（pjproject 2.17）

本项目（`teleflow-lite`）通过 `src/teleflow/pjsua2_backend.py` 与 `src/teleflow/audio.py`
**懒加载** `import pjsua2 as pj`，因此需要把 pjsua2 的原生 Python 模块编译并安装进项目的
`.venv`。本文记录从源码构建 pjproject 2.17 并产出 `_pjsua2.so` 的完整流程，以及踩过的坑。

> 📦 **pjsua2 现已以「本地 wheel」形式 vendored**（`dist/`，见下方「打包成 wheel」
> 一节），并作为**可选 extra**写进 `pyproject.toml`（`[project.optional-dependencies].pjsua2`）。
> 因此 `uv sync` 装好其余依赖、`uv sync --extra pjsua2` 才安装它——把它设为可选 extra，是为让
> 干净的 `uv sync` 在 wheel 还不存在时于各平台都能顺利执行。只有当 **Python 版本或 CPU 架构变化**
> 时才需要按本文从源码重编，然后跑 `scripts/build_pjsua2_wheel.py` 重新打包（该脚本会自动执行
> `uv sync --extra pjsua2` 装好）。重编 SWIG 模块前若缺 `setuptools` / `wheel`（它们也在 `dev`
> 依赖组里），先 `uv pip install setuptools wheel`。本文件里的 `.venv/bin/python` 路径在 uv 创建的
> 虚拟环境里依然有效。

环境：macOS arm64（Apple M），clang 17，`/opt/homebrew` 与 `/opt/local`（MacPorts）并存。

---

## 1. 准备依赖（本机已具备，仅作核对）

| 工具 | 命令/路径 | 说明 |
| --- | --- | --- |
| GNU make | `/usr/bin/make`（3.81） | 系统自带即可，pjsip 用 autoconf 构建 |
| C/C++ 编译器 | `/usr/bin/g++`（Apple clang 17） | pjsua2 是 C++，需要 CXX |
| SWIG | `/opt/homebrew/bin/swig` | 生成 Python 包装代码 |
| Python | 项目 `.venv`（由 uv 创建，Python 3.14；需含 setuptools 84，缺失时用 `uv pip install setuptools` 补装） | **必须用 venv 里的 python 编 SWIG 模块** |
| OpenSSL | `/opt/homebrew/opt/openssl@3` | macOS 上 pjsip 默认用 Darwin SSL，无需手动指定；openssl 仅被动态链接 |

无需 `autoconf`/`pkg-config`（源码包已带 `configure` 脚本）。

---

## 2. 编译流程

```bash
# 源码已下载解压到 /tmp/pjproject-2.17（或任意目录）
cd /tmp/pjproject-2.17

# (1) 配置：默认即可。macOS 自动启用 Darwin SSL，pjsua2 默认开启。
./configure

# (2) 编译静态库（含 libpjsua2-*.a）
make dep
make

# (3) 编译 Python SWIG 模块。关键：把 PYTHON_EXE 指向 venv 的 python，
#     否则会用系统 /opt/homebrew/bin/python3（缺 setuptools，见坑 1）。
cd pjsip-apps/src/swig/python
make PYTHON_EXE=/Users/zhouteng/Documents/workspace/teleflow-lite/.venv/bin/python
# 产物：build/lib.macosx-*/_pjsua2.cpython-314-darwin.so + pjsua2.py

# (4) 安装进 venv 的 site-packages（不要 make install，见坑 2）
/Users/zhouteng/Documents/workspace/teleflow-lite/.venv/bin/python setup.py install
```

安装结果：

```
.venv/lib/python3.14/site-packages/pjsua2.py
.venv/lib/python3.14/site-packages/_pjsua2.cpython-314-darwin.so
```

---

## 3. 验证

```bash
.venv/bin/python -c "import pjsua2 as pj; print(pj.Endpoint)"
# 进一步做功能性验证（真正初始化 PJLIB）：
.venv/bin/python -c "
import pjsua2 as pj
ep = pj.Endpoint()
ep.libCreate()        # 应输出 pjlib 2.17 for POSIX initialized
ep.libDestroy()
print('OK')
"
```

预期：`libCreate()` 后打印 `pjlib 2.17 for POSIX initialized` 与 PJSUA 状态机
`CREATED → CLOSING → NULL`，即说明原生库可正常工作。

> 注：本机因已安装 SDL2（/opt/local）与 ffmpeg（/opt/homebrew），configure 会自动
> 开启视频支持（`PJMEDIA_HAS_VIDEO`、SDL/ffmpeg/VideoToolbox 后端）。对纯音频的
> teleflow-lite 无影响，只是多编了一些编解码/设备后端，可忽略。

---

## 4. 打包成 vendored wheel（供 uv 依赖）

上面把 `pjsua2` 装进了 `.venv`，但 `.venv` 由 uv 管理、`uv sync` 会重建它，手装的模块会丢。
为让 `pjsua2` 成为可重装的依赖、随 `uv sync --extra pjsua2` 自动安装，把它打包成本地 wheel 放进仓库
根目录的 `dist/`（`dist/` 已在 `.gitignore` 中忽略，仅同平台可用）。`pyproject.toml` 通过
`[tool.uv] find-links = ["dist"]` 把它声明为**扁平索引**：uv 按名字 `pjsua2` 从 `dist/` 目录里挑
**匹配当前平台 tag 的 wheel**，因此提交的 pyproject.toml 里**没有任何平台相关的文件名**，macOS /
Windows 用同一份文件即可：

```bash
# 从当前 .venv 里已编好的 pjsua2 生成 wheel（top-level: pjsua2.py + _pjsua2*.so）。
# 用 venv 的 python 跑脚本（跨平台，Windows 用 .venv\Scripts\python.exe）：
.venv/bin/python scripts/build_pjsua2_wheel.py
# 产物示例：dist/pjsua2-2.17-cp314-cp314-macosx_11_0_arm64.whl（Windows 为 win_amd64）
```

`build_pjsua2_wheel.py` 会**自动探测**当前 Python 版本与平台，把正确的 wheel tag 写进 WHEEL 元数据
（并清掉 `dist/` 里旧平台的 wheel），然后自动跑 `uv sync --extra pjsua2` 安装。**不需要改写
pyproject.toml。**

`pyproject.toml` 里对应的声明：

```toml
# 可选 extra：默认依赖不含 pjsua2，`uv sync` 在任何平台都顺利执行（测试用 FakeSipBackend，
# 不需要原生库）。marker 限定在 vendored 的平台（macOS arm64 / Windows win_amd64，均 cp314）。
[project.optional-dependencies]
pjsua2 = ["pjsua2 ; (sys_platform == 'darwin' and platform_machine == 'arm64' and python_version == '3.14') or (sys_platform == 'win32' and platform_machine == 'AMD64' and python_version == '3.14')"]

# 扁平索引：uv 扫描 dist/ 目录、按 wheel 的 platform tag 匹配当前主机，按名解析 pjsua2。
# 提交的文件里不出现在何平台文件名，macOS / Windows 共用。
[tool.uv]
find-links = ["dist"]
```

要点：
- pjsua2 是 **原生扩展**，一个 wheel 无法跨平台。因此 pjsua2 作为 **可选 extra** 提供（不在默认
  依赖里），marker 限定在 macOS arm64 / Windows win_amd64（均 cp314）；`uv sync` 默认跳过它（测试用
  FakeSipBackend，不需要它），在任何平台都不会报错。需要真实传输时在本机构建后 `uv sync --extra
  pjsua2` 安装。换 Python 版本或架构时按第 2 节重编，再跑
  `scripts/build_pjsua2_wheel.py`（自动打包并安装）。
- 不用 `path` source 指向 wheel 文件的原因：`[tool.uv.sources]` 的 path（无论单条目还是带 marker
  的数组）在 `uv sync` 解析阶段都会**读取该文件**，只要文件名里带平台 tag、而那份文件在另一平台
  的 `dist/` 里不存在，对方平台的 `uv sync` 就会直接失败。find-links 按名解析则只读匹配当前平台
  tag 的 wheel，平台文件名完全不出现在 pyproject 里。
- wheel 用 `SOURCE_DATE_EPOCH=0` 打包，产物可复现（相同输入 hash 不变），`uv.lock` 不会因重打包而失配。

---

## 5. 踩过的坑

### 坑 1：系统 `python3` 没有 setuptools / distutils，SWIG 模块编不过

SWIG 的 `Makefile` 默认 `PYTHON_EXE=python3`，在本机解析到
`/opt/homebrew/bin/python3`（3.14）。Python 3.12+ 已移除 `distutils`，而该 Homebrew
python 也未装 `setuptools`，于是 `setup.py build` 报：

```
ModuleNotFoundError: No module named 'setuptools'
ModuleNotFoundError: No module named 'distutils'
```

**解决**：把 `PYTHON_EXE` 指向项目的 venv python（自带 setuptools 84 且含 C 头文件）：

```bash
make PYTHON_EXE=/.../teleflow-lite/.venv/bin/python
```

### 坑 2：`make install` 会把模块装到 `~/.local` 而非 venv

SWIG 的 install target 是 `$(PYTHON_EXE) setup.py install --user`。在 venv 激活态下
`--user` 仍然指向用户目录（`~/.local`），而不是 venv 的 `site-packages`，导致项目运行时
`import pjsua2` 找不到。

**解决**：直接用 venv python 跑 `setup.py install`（不带 `--user`），模块即落入
`.venv/lib/python3.14/site-packages`：

```bash
.venv/bin/python setup.py install
```

### 坑 3：`PJ_EXCLUDE_PJSUA2 := 1` 看似禁用了 pjsua2，其实是误读

`build.mak` 第 29 行有：

```make
ifeq (,1)
export PJ_EXCLUDE_PJSUA2 := 1
endif
```

`ifeq (,1)` 为假（`ac_no_pjsua2` 为空，不等于 `1`），所以里面的 `export` 不会执行，
pjsua2 **并未被排除**。`./configure` 的输出也已确认
`Building pjsua2 library and application... yes`。看到这串文本先别慌，确认
`pjsip/lib/libpjsua2-*.a` 存在即可。

### 坑 4：运行时动态库依赖

`_pjsua2.so` 把 pjsip 各静态库（`libpjsua2`、`libpjmedia`…）**静态链接**进自身，因此运行时
只需要 `pjsua2.py` + `_pjsua2*.so` 两个文件。但它仍会动态链接系统框架
（`CoreAudio`/`AudioUnit`/`AVFoundation`…）以及 `libssl`/`libcrypto`/`libSDL2`/ffmpeg
等。这些库在链接时已把**绝对 install name** 写进 `.so`，无需设置 `DYLD_LIBRARY_PATH`。
若换机或重装 Homebrew/MacPorts 后导入报 `image not found`，需重新执行第 2 节步骤 3–4。

---

## 6. 何时需要重新构建

- pjproject 升级到新版本时；
- 换了 **Python 版本或 CPU 架构**（wheel 是 cp314 / macOS arm64 专用，须按第 2 节重编后
  重新跑 `scripts/build_pjsua2_wheel.py` 打包，再 `uv sync --extra pjsua2`）；
- 删除并重建 `.venv` 后（只要有 `uv.lock` + 本地 wheel，`uv sync --extra pjsua2` 会自动重装
  pjsua2，无需手编）；
- 升级 macOS / Xcode 导致 clang 或系统框架变化、且 `.so` 动态链接的系统库（见坑 4）变动后。

流程不变，照第 2 节重跑即可。

---

## 7. Windows（MSYS2 / MinGW-w64 UCRT）

本机为 Windows（zh-CN），CPython 3.14.2（pyenv-win / scoop）。pjsua2 原生模块在 Windows 上
**必须用 MSYS2 的 MinGW-w64（UCRT）工具链编译，不能用 Visual Studio**：pjsip 2.17 的 Python
wrapper（`pjsip-apps/src/swig/python`）硬绑 GNU make + `helper.mak`，其 `Makefile` 强制
`--compiler=mingw32`；CMake 只能编 C 静态库、编不出 Python wrapper。VS 2022 BuildTools 的
`vcvars64.bat` 虽能编出 C 静态库，但 wrapper 走不通，故全程 MinGW。

打包成 vendored wheel 的流程与 macOS 完全一致：`scripts/build_pjsua2_wheel.py` 会自动探测
Windows 平台、把正确的 `win_amd64` tag 写进 WHEEL 元数据并装入 `dist/`（同时清掉旧平台的 wheel），
随后 `uv sync --extra pjsua2` 即从 `dist/` 按名安装该 wheel（构建脚本已自动执行此步，无需手跑
`uv lock`/`uv sync`，也无需改 pyproject）。下面只列与 macOS 不同的
「编原生模块」部分；第 4 节「打包成
vendored wheel」照搬即可（把 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`）。

### 7.1 准备依赖

从 <https://www.msys2.org> 装 MSYS2，打开 **ucrt64** shell（不是默认 msys shell），安装：

```bash
pacman -S mingw-w64-ucrt-x86_64-toolchain mingw-w64-ucrt-x86_64-swig
```

| 工具 | 说明 |
| --- | --- |
| `mingw-w64-ucrt-x86_64-toolchain` | gcc/g++/make。**UCRT** 运行时与 MSVC 版 CPython 3.14 的 CRT 一致，编出的 `.pyd` 才能被 CPython 加载（用 MSVCRT 工具链会 CRT 不匹配、导入失败）。 |
| `mingw-w64-ucrt-x86_64-swig` | 生成 Python 包装代码（也可用 scoop 的 `swig`，但 `make` 必须走 ucrt64 的 mingw32 版）。 |
| Python | 项目 `.venv` 由 uv 创建，3.14.2（pyenv-win / scoop），含 `include/Python.h` 与 `libs/python3.lib`，C 扩展可正常编。 |
| pjsip 2.17 源码 | 从 GitHub tag 归档下载（pjsip.org 旧路径已 404）：`https://github.com/pjsip/pjproject/archive/refs/tags/2.17.tar.gz`，解压出 `pjproject-2.17/`。 |

> 网络注意：本机若走 `127.0.0.1` 代理可能拉不动 HTTPS，必要时 `--noproxy` 或临时关闭代理再下载。

### 7.2 关闭 OpenSSL/TLS（音频专用，免依赖）

teleflow-lite 只做音频 SIP，不需要 TLS/SRTP。在 pjproject 树里新建
`pjlib/include/pj/config_site.h`：

```c
#ifndef CONFIG_SITE_H
#define CONFIG_SITE_H
#define PJ_HAS_SSL_SOCK 0
#define PJ_HAS_OPENSSL 0
#endif
```

这关掉 OpenSSL 依赖，`configure` 不会再要求 OpenSSL。

### 7.3 编译（在 ucrt64 shell 里）

```bash
cd pjproject-2.17
./configure
make dep
make                                  # 产出 libpjsua2-*.a 等静态库

# 编 SWIG Python 模块：把 PYTHON_EXE 指向 venv 的 python（含 setuptools）
cd pjsip-apps/src/swig/python
make PYTHON_EXE=/c/Users/zhouteng/Documents/workspace/teleflow-lite/.venv/Scripts/python.exe
# 产物：build/lib.win-amd64-*/_pjsua2*.pyd + pjsua2.py
```

> 坑：不要用系统 `python3` 或 MSVC 的 `vcvars` —— wrapper 的 `Makefile` 强制
> `--compiler=mingw32`，必须用 ucrt64 的 mingw make 与工具链，否则编不过。

### 7.4 安装进 venv

```bash
/c/Users/zhouteng/Documents/workspace/teleflow-lite/.venv/Scripts/python.exe setup.py install
```

> 不要 `make install`（它会 `--user` 装到 `~/.local`，绕过 venv）。直接用 venv python 跑
> `setup.py install`（不带 `--user`），模块落入 `.venv\Lib\site-packages`。

安装结果（注意是 `.pyd` 而非 `.so`）：

```
.venv\Lib\site-packages\pjsua2.py
.venv\Lib\site-packages\_pjsua2.cp314-win_amd64.pyd
```

### 7.5 验证

```powershell
.venv\Scripts\python.exe -c "import pjsua2 as pj; print(pj.Endpoint)"
.venv\Scripts\python.exe -c "import pjsua2 as pj; ep=pj.Endpoint(); ep.libCreate(); ep.libDestroy(); print('OK')"
```

### 7.6 打包成 vendored wheel（供 uv 依赖）

```powershell
.venv\Scripts\python.exe scripts\build_pjsua2_wheel.py
```

`build_pjsua2_wheel.py` 已原生支持 Windows：`detect_tag` 对 `win32` 产出 `win_amd64` 小写
tag（PEP 508 marker 仍用 `platform_machine == 'AMD64'` 真实大小写），并 glob `_pjsua2*.pyd`。
随后 `uv sync --extra pjsua2` 安装该 wheel（构建脚本已自动执行此步，无需手跑 `uv lock`/`uv sync`）。

> **平台互斥**：同一仓库下 `dist/` 只会保留当前平台的 wheel；pyproject 通过 `find-links` 按名
> 解析、按 wheel tag 匹配平台，macOS / Windows 共用同一份提交的 pyproject，换平台构建时重跑
> 本脚本即可。
> **WMME 设备名**：zh-CN Windows 下 pjsua2 返回的 WMME 设备名是 UTF-8 surrogate-escaped 的
> ANSI（GBK）字节，`src/teleflow/audio.py` 的 `_recover_wmme_names` 已用 `mbcs` 还原，编译期
> 无需特别处理。

