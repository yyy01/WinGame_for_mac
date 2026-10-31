# 验证记录

日期：2026-10-31。组件版本：0.1.0。主机：Apple M2 / 24 GB / macOS 14.5。实际游戏使用原版 Windows GoldSrc 与 Valve 游戏 DLL，Wine 10.0 Sikarugir revision 6，MSYNC。

## 本轮组件验证

| 项目 | 结果 | 实际覆盖 |
|---|---|---|
| 自动测试 | passed | 20 项 Python 功能测试＋1 项 macOS 原生集成测试；源码与安装后的 wheel 均执行 |
| 原生入口 | passed | 本机 arm64 Cocoa 编译，ad-hoc 签名并严格验签 |
| 图片与图标 | passed | 系统图片转码、完整 ICNS 生成、Info.plist 资源引用；未提供新的实际封面素材 |
| 参数与生命周期 | passed | 假 Wine 收到完整字面参数，含空格/中文/shell 符号；正常退出后释放会话锁 |
| 配置与数据隔离 | passed | 外部链接/目录逃逸拒绝、导入失败回滚、源配置不受副本写入影响、Steam/prefix 不能当游戏导入 |
| 诊断隐私 | passed | 不导出私有路径、原始日志或账户状态；本机配置文件 0600、根目录 0700 |
| wheel 分发 | passed | 独立环境安装后 CLI 可运行，随包携带 Objective-C 与 C 源码，重新编译原生入口 |
| CS 1.6 新入口 | passed | 新入口连接现有 Steam，进入原版菜单，通过原版 Quit 对话框退出，互斥锁释放 |
| Condition Zero 新入口 | passed | 原版主菜单、创建本地对局、cs_backalley、加入 CT、记分板 9 bots＋1 player |
| 其他 Windows 游戏 | not_run | 提供通用清单示例，不据此声明其他引擎或 DirectX 游戏兼容 |

GitHub Actions 以 Linux Python 3.10 / 3.14 与 macOS Python 3.11 执行组件测试。这里的本机通过记录不代替远端 CI 的实际运行状态。

## 已有 CS 适配的核验范围

在本轮组件化之前，同一套独立原版游戏副本已核验：14,040 个原版资源文件、245 张 BSP 地图；32 个引擎文件及 6 个游戏 DLL 对照原版哈希。仅在兼容副本中去除了 17 个不用的 Mac/Linux 动态库。原始 Steam 安装保持独立。

此前实际交互覆盖：原版菜单与选项页、Dust2 本地对局、选队/人物、移动、瞄准、弹痕、换弹、沙鹰购买、窗口/全屏切换、零点行动 Office 九机器人、正常退出。声音由用户确认可听到。该范围属于抽样验收，不能证明所有地图、全部武器、长期稳定性和 Windows 逐帧行为完全一致。

Deleted Scenes 保持此前的测试入口，其剧情流程、存档/读取曾试运行；游戏内退出自动化尚不可靠。本仓库不将它列为完整通过的游戏适配。

## 性能验证方法和限制

原生包装器只在启动时等待 Steam，游戏运行期间等待进程结束，不进行渲染或逐帧输入轮询。启动日志限制为 2 MiB，失败探测退避，等待使用真实截止时间。APFS 克隆节省初次复制的时间与物理空间，但并不提高游戏帧率。

同步模式需要同一段录制、同一分辨率、同一画质与帧率设置对照。推荐：

1. 在原版游戏控制台 `record local_benchmark`，完成固定路径后 `stop`。
2. 记录 demo、exe、主要 DLL 与相关 cfg 的 SHA-256，保持资源和画质一致。
3. 使用原版 `timedemo local_benchmark`；一次预热后至少重复三次。回放期间不打开控制台、不切窗口、不截图。
4. 完整退出游戏，使用 `wgfm sync` 停止同一 prefix 并切换模式，再重复相同测量。
5. 报告全部样本、统计量和限制；回放 FPS 不能替代机器人 AI、输入延迟、1% low、发热与长时间稳定性。

此前的 Office HUD 54 / 71 / 80 FPS 只是三个瞬时截图，不能当作平均帧率。没有原生 Windows 对照，不能宣称已经达到“无差异”或“最大性能”。保持原版画质与游戏逻辑是本项目的约束，不通过降画质或改游戏参数制造优化结果。

## 发布内容

仓库只包括组件源码、示例、测试、许可和文档。没有游戏、Wine 引擎、Steam 客户端、登录 prefix、账号文件、存档、原始日志或测试截图。程序内的 `diagnostics` 也只导出允许公开的检查字段。

引擎和框架参考为官方发行版本：[Sikarugir Engines](https://github.com/Sikarugir-App/Engines/releases/tag/v1.0)、[Sikarugir Template](https://github.com/Sikarugir-App/Template/releases/tag/v1.0)，固定指纹见 `examples/runtime-reference.json`。
