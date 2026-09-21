# Haru · 日语，在日常里

面向中文母语学习者的 macOS / Windows 日语学习 App，从零基础开始，用中文讲解、日语朗读和日常练习逐步入门。

## 下载安装（推荐）

前往 [GitHub Releases](https://github.com/U1XOvO/Haru/releases)，选择与你的电脑匹配的 ZIP：

| 系统 | 安装包后缀 | 启动方式 |
| --- | --- | --- |
| Windows 10/11 x64 | `windows-x64.zip` | 完整解压，双击 `Haru/Haru.exe` |
| macOS 14+，Apple Silicon | `macos-arm64.zip` | 解压，将 `Haru.app` 拖入「应用程序」 |
| macOS 14+，Intel | `macos-x64.zip` | 解压，将 `Haru.app` 拖入「应用程序」 |

Release 包内置运行环境，无需安装 Python、uv 或开发工具。Windows 需要 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)；不要只复制单个 `.exe`。若 Releases 尚无安装包，可以使用下面的源码启动方式。

当前 Windows 包未做发布者签名，macOS 包使用临时签名、尚未公证，首次运行可能显示系统来源提示。请核对仓库与下载来源后按系统提示打开。

启动后，在「偏好设置 → AI 连接」填写服务商的 API 地址、密钥和模型 ID，点击「保存并测试连接」。需要兼容 OpenAI 接口、支持 JSON 输出的模型。打开「每日课程」，选择第一课，点击「AI 创建课程」开始学习。

### 数据与更新

| 运行方式 | 配置 | 学习记录与录音 |
| --- | --- | --- |
| Windows Release | `%LOCALAPPDATA%\Haru\.env` | `%LOCALAPPDATA%\Haru\runtime\` |
| macOS Release | `~/Library/Application Support/Haru/.env` | `~/Library/Application Support/Haru/runtime/` |
| 源码启动、本机从源码生成的 Windows App | 项目 `.env` | 项目 `runtime/` |

更新 Release 时先退出 App，再替换完整的应用文件夹；不要删除上述用户数据目录。macOS 从源码生成的 `Haru.app` 与 Release 包是两种构建方式，前者仍依赖项目目录。

**从源码版迁移记录**：先退出两种版本并备份项目 `.env` 和整个 `runtime/`，再将它们复制到对应 Release 的 Haru 用户数据目录。仅在目标位置尚无配置与记录时复制；如两边已有数据，保留各自备份，不直接覆盖数据库。跨系统迁移的录音格式可能不同。

## 从源码运行

1. `git clone` 或下载并解压项目，放到准备长期保留的位置。
2. 根据系统双击启动入口；首次运行需要联网，自动准备项目专用的 uv、Python 3.12 和锁定依赖，无需预先安装 Python。
   - **macOS 14+**：双击 `start.command`。若提示缺少 Apple 命令行工具，在终端执行 `xcode-select --install`，安装后重试。
   - **Windows 10/11（x64）**：首次双击 `start.cmd`，自动构建并打开独立的 **Haru App**（首次打包需要几分钟）。之后双击项目内的 **Haru 快捷方式**或 `dist\Haru\Haru.exe`，无需打开 Python 或命令行。需要 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)；若提示缺失，请安装官方 x64 Evergreen Runtime 后重试。
3. 在「偏好设置 → AI 连接」填写服务商的 API 地址、密钥和模型 ID，点击「保存并测试连接」。需使用兼容 OpenAI 接口、支持 JSON 输出的模型服务。
4. 打开「每日课程」，选择第一课，点击「AI 创建课程」开始学习。

若无法双击启动，macOS 在项目目录运行 `bash start.command`，Windows 在 PowerShell 中运行 `.\start.cmd`。已安装 Python 的系统也可以统一运行 `python start.py`，它会自动识别 macOS / Windows 并选择对应启动流程。Linux 和 Windows ARM64 暂不支持。

macOS 以后仍使用启动入口打开；Windows 日常使用 Haru 快捷方式，更新代码后关闭 App 并重新运行 `start.cmd`，脚本会按需重新打包。移动项目后请重新运行入口以更新路径。跨系统请重新克隆，不要复制 `.venv`、`.tools` 或已构建的 App。本机从项目构建的 App 继续使用项目 `.env` 和 `runtime/`，均不会提交到 Git。

### 本机构建 Windows App

- App 内置 Python 和所需依赖，使用 Haru 名称与图标，日常启动没有控制台窗口。首次构建时的终端会在 App 启动后退出。
- 生成位置为 `dist\Haru\Haru.exe`；可以把项目中的 `Haru.lnk` 快捷方式复制到桌面。
- 分享给其他 Windows x64 电脑时，复制整个 `dist\Haru` 文件夹，不能只复制 `.exe`。接收方无需安装 Python，但仍需 WebView2 Runtime。
- 独立分发的 App 将配置和学习记录保存在 `%LOCALAPPDATA%\Haru`；打包不会包含你的密钥或学习记录。保留此目录即可在替换 App 文件夹时保留记录。
- 仅准备环境和构建、不打开 App：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_windows.ps1 -SetupOnly`。Windows App 必须在 Windows 上构建。

双平台 Release 的构建命令、版本管理与 GitHub 发布步骤见 [RELEASING.md](RELEASING.md)。源码提交、Pull Request 和手动触发 Actions 都会构建三个平台包；推送匹配版本的 `v*` 标签后，检查全部通过才创建 Draft Release。

### Windows 语音与录音

- 日语朗读使用 Windows 桌面语音引擎；若提示无可用日语语音，请在系统语言/语音设置中添加日语语音包，完成后重启应用。可用音色取决于系统安装的桌面语音。
- 跟读录音需要开启「设置 → 隐私和安全性 → 麦克风 → 允许桌面应用访问麦克风」。录音最长 60 秒，保存为 `runtime/speaking-latest.wav`，不会上传。
- 导入的 MP3/M4A/WAV 使用系统解码器播放；不支持的编码会显示错误，可改用 WAV 文件。
- 启动脚本只对当前 PowerShell 进程使用执行策略参数，不修改系统全局策略或 PATH。首次环境准备失败时终端会保留错误信息，修复网络或运行库后再次启动即可。

## 可以学什么

- **每日课程**：中文讲解、假名、例句、听读与课后练习。
- **即时学习卡**：生成词卡，按记忆情况安排复习。
- **真实对话与语法解码器**：练习情景交流，理解日语句子。
- **语法与 JLPT**：学习 N5–N1 语法，练习官方问题集与 AI 模拟题。
- **沉浸日语与学习进度**：阅读短篇故事，查看进度、参加周测。

## 使用须知

- AI 功能需要联网，相关输入会发送给你配置的服务商，并使用你的 API 额度。每次打开 App 时，首页也会自动生成「今日的一点日语」。AI 内容可能有误。
- 浏览课程列表不会自动生成课程；已保存的课程和词卡可离线复习。
