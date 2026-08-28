# 编译 pjsua2 原生模块（pjproject 2.17）

本项目（`teleflow-lite`）通过 `src/teleflow/pjsua2_backend.py` 与 `src/teleflow/audio.py`
**懒加载** `import pjsua2 as pj`，因此需要把 pjsua2 的原生 Python 模块编译并安装进项目的
`.venv`。本文记录从源码构建 pjproject 2.17 并产出 `_pjsua2.so` 的完整流程，以及踩过的坑。

环境：macOS arm64（Apple M），clang 17，`/opt/homebrew` 与 `/opt/local`（MacPorts）并存。

---

## 1. 准备依赖（本机已具备，仅作核对）

| 工具 | 命令/路径 | 说明 |
| --- | --- | --- |
| GNU make | `/usr/bin/make`（3.81） | 系统自带即可，pjsip 用 autoconf 构建 |
| C/C++ 编译器 | `/usr/bin/g++`（Apple clang 17） | pjsua2 是 C++，需要 CXX |
| SWIG | `/opt/homebrew/bin/swig` | 生成 Python 包装代码 |
| Python | 项目 `.venv`（`python3.14`，含 setuptools 84） | **必须用 venv 里的 python 编 SWIG 模块** |
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

## 4. 踩过的坑

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

## 5. 何时需要重新构建

- pjproject 升级到新版本时；
- 删除并重建 `.venv` 后（模块装在 venv 内，重建即丢失）；
- 升级 macOS / Xcode 导致 clang 或系统框架变化后。

流程不变，照第 2 节重跑即可。
