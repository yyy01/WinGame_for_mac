# WinGame for Mac

将本机已有的 Windows 游戏、Wine 运行时和独立配置组合成原生 macOS `.app` 启动器。公共仓库保存适配代码与游戏清单；游戏资源、Wine、Steam、账号与存档保存在用户本机。

当前版本 **0.1.0**。已在 Apple M2 / macOS 14.5 上验证 Counter-Strike 1.6 与 Condition Zero 的原版 Windows 引擎。其他游戏需要逐款验证；这不是把 Windows 二进制重新编译成 ARM 原生游戏的工具。图形 API、DRM、系统组件和反作弊兼容性仍由所选 Wine 运行时与游戏决定。

## 提供什么

- JSON 游戏清单：相对可执行路径、工作目录、独立参数数组、Steam App ID。
- 可复用运行时登记：固定文件指纹，独立 Wine prefix；有意共享 prefix 的游戏使用同一运行时和同步模式。
- 原生 Cocoa 启动器：Apple 芯片本机编译，启动时不依赖 Python、网页框架或 Node。
- Steam 就绪检查、实际时间超时、取消等待、同一 prefix 互斥启动、2 MiB 有界日志。
- 游戏导入优先使用 APFS 写时复制，失败后普通复制。原始安装目录保持独立；也支持明确引用现有副本。
- 启动器封面、Mac `.icns` 图标替换；构建时缩放图片、生成多尺寸图标并本地签名。
- 文件检查、原版资源 SHA-256 对照、只含白名单结果的诊断导出。

默认不会添加改动帧率、分辨率、鼠标、画质、机器人、武器或游戏规则的参数。CS 示例仅使用原版 `-game` 与兼容性参数 `-nobreakpad`。

## 安装组件

需要 macOS 11+、Python 3.10+、Xcode Command Line Tools，以及一套适合游戏的已安装 Wine。Apple 芯片运行这里验证过的 Intel Wine 引擎还需要 Rosetta 2；组件不会自动代替用户接受它的安装协议。开发与实际验证环境见 [验证记录](docs/VALIDATION.md)。

```sh
git clone git@github.com:yyy01/WinGame_for_mac.git
cd WinGame_for_mac
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/wgfm --help
```

Python 用于配置和构建；生成的 `.app` 用系统 Cocoa 直接启动 Wine。应用是本机启动入口，不包含运行时和游戏，不能仅复制一个 `.app` 就在另一台电脑游玩。

## 接入一款游戏

先准备合法拥有的 Windows 游戏目录，及能运行它的 Wine。不要把整个 Steam 客户端目录或 Wine prefix 当作游戏导入。

```sh
# 替换下列路径。wine 可以是带 Cocoa 元数据的 Wine.app 内加载器。
.venv/bin/wgfm runtime-add my-wine \
  --wine '/path/to/Wine.app/Contents/MacOS/wine' \
  --wineserver '/path/to/runtime/bin/wineserver' \
  --library '/path/to/Frameworks' \
  --sync server

# 复制 examples/standalone.json，填写实际 exe、名称和参数。
.venv/bin/wgfm validate examples/standalone.json
.venv/bin/wgfm game-add examples/standalone.json \
  --source '/path/to/owned-game' --runtime my-wine
.venv/bin/wgfm doctor my-game
.venv/bin/wgfm build my-game --output "$HOME/Applications"
open "$HOME/Applications/My Game Mac.app"
```

`--sync server` 是保守的通用初始值。`msync` / `esync` 必须由运行时支持并经该游戏实测；不要同时启用两种。已经适配好的游戏可以使用 `--adopt-existing` 直接引用其独立游戏副本，避免再复制。此时游戏自身写入的配置和存档也会写进这份副本。

## Steam / CS 示例

Steam 游戏需要本机 Windows Steam、该游戏授权、同一个 prefix，以及与游戏位数一致的就绪检查程序。组件不会下载游戏、复制账号或绕过授权。可参考已验证的运行时版本与哈希：[runtime-reference.json](examples/runtime-reference.json)。该文件用于版本对照，CLI 不会自动下载或安装运行时。

就绪程序源码随包提供于 `src/wgfm/resources/steam_ready.c`。使用已安装的 MinGW-w64 编译器构建；它动态加载**游戏本身**的 `steam_api.dll`，无需分发 Steam SDK：

```sh
# 32 位游戏，包括 CS 1.6 / Condition Zero
i686-w64-mingw32-gcc -Os -s src/wgfm/resources/steam_ready.c -o /path/to/tools/SteamReady.exe
# 64 位游戏改用 x86_64-w64-mingw32-gcc。

.venv/bin/wgfm game-add examples/counter-strike.json \
  --source '/path/to/Windows-Half-Life' --runtime my-wine \
  --prefix '/path/to/private-prefix' \
  --steam '/path/to/Windows-Steam/steam.exe' \
  --probe '/path/to/tools/SteamReady.exe'
.venv/bin/wgfm doctor counter-strike
.venv/bin/wgfm build counter-strike
```

同一 GoldSrc 安装的 Condition Zero 使用 `examples/condition-zero.json`；若共享 prefix，必须引用同一个 runtime ID。启动器只允许其中一个游戏同时运行，退出后自动释放锁。Steam 可以保持后台运行以便下次启动。

## 更换封面和应用图标

支持 PNG/JPEG 等系统可解码图片；图标也可直接使用 `.icns`。建议封面使用横图、图标使用方图，每张输入不超过 32 MiB。构建时封面最大边缩到 1600 px，图标生成 16–1024 px 多尺寸版本。

```sh
.venv/bin/wgfm artwork counter-strike \
  --cover '/path/to/cover.jpg' --icon '/path/to/icon.png'
.venv/bin/wgfm build counter-strike --replace
```

新应用替换前，旧版保留为 `.app.previous`；再次替换前请先移走这份备份。图标和封面由构建结果携带，源图片丢失不会影响已构建应用。Finder/Dock 可能缓存旧图标，退出旧程序后从新应用启动即可。

这里修改的是**启动器封面和 Mac 应用图标**。游戏内主菜单背景与 Steam 游戏库封面属于各自独立的资源和平台设置，当前组件不修改它们。

## 检查与维护

```sh
# 检查文件、架构、指纹与共享环境配置；不等于游戏兼容性验收。
.venv/bin/wgfm doctor counter-strike

# 原版资源基线：在实际游玩之前生成。
.venv/bin/wgfm inventory '/path/to/original-game' --output /tmp/game-baseline.json
.venv/bin/wgfm check-inventory /tmp/game-baseline.json '/path/to/adapted-game'

# 只导出布尔检查、版本、游戏 ID 与同步模式；不复制日志、路径或账户文件。
.venv/bin/wgfm diagnostics counter-strike --output /tmp/diagnostics.json

# 游戏退出后停止该 prefix 的全部 Windows 进程；不会操作其他 prefix。
.venv/bin/wgfm stop counter-strike
# 先停止 prefix 再切换模式，并同步更新有意共享它的所有注册游戏。
.venv/bin/wgfm sync counter-strike msync
```

本机状态默认保存在 `~/Library/Application Support/WinGameForMac`。`--home` 可指定其他私有位置，应放在仓库之外。不要发布这整个目录。日志可能包含游戏自己输出的信息，保留本机供排查，不会随 diagnostics 导出。详细约定见 [架构与清单](docs/ARCHITECTURE.md) 和 [验证记录](docs/VALIDATION.md)。

## 开发

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m pip wheel . --no-deps --wheel-dir /tmp/wgfm-wheel
```

自动测试覆盖导入回滚、路径隔离、原始配置保护、PE 架构、环境变量、共享锁、私有权限、诊断过滤，以及 macOS 原生构建/图片编码/参数传递/进程退出。CI 使用 Linux 与 macOS；假 Wine 测试不代表真实游戏已通过。游戏交互需按验证清单逐款实测。

本仓库自有代码使用 MIT 许可。Wine、Steam、游戏资源、图形转换组件与封面素材保留各自的许可，不随本仓库重新授权或分发。
