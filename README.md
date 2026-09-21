# Haru · 日语，在日常里

面向中文母语学习者的 macOS / Windows 日语学习 App，从零基础开始，用中文讲解、日语朗读和日常练习逐步入门。

## 克隆并启动

先安装 Git，然后在终端运行：

```sh
git clone https://github.com/U1XOvO/Haru.git
cd Haru
```

将项目放在准备长期保留的位置。首次运行需要联网，启动脚本会自动准备项目专用的 uv、Python 3.12 和锁定依赖，无需预先安装 Python。

| 系统 | 启动入口 | 系统要求 |
| --- | --- | --- |
| macOS 14+（Apple Silicon / Intel） | 双击 `start.command`，或运行 `bash start.command` | Apple 命令行工具；缺少时运行 `xcode-select --install`，安装完成后重试 |
| Windows 10/11 x64 | 双击 `start.cmd`，或在 PowerShell 运行 `.\start.cmd` | [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)；缺少时安装官方 x64 Evergreen Runtime |

macOS 会自动构建并打开项目内的 `Haru.app`。Windows 首次会在本机构建 `dist\Haru\Haru.exe` 并创建项目内的 `Haru.lnk` 快捷方式，可能需要几分钟；完成后自动打开 App，日常使用快捷方式即可，不显示控制台窗口。

已安装 Python 的系统也可以运行 `python start.py`，它会自动识别 macOS / Windows 并选择对应启动流程。Linux 和 Windows ARM64 暂不支持。

### 首次使用

1. 在「偏好设置 → AI 连接」填写服务商的 API 地址、密钥和模型 ID，点击「保存并测试连接」。需使用兼容 OpenAI 接口、支持 JSON 输出的模型服务。
2. 打开「每日课程」，选择第一课，点击「AI 创建课程」开始学习。

### 更新与本地数据

- 配置保存在项目 `.env`，学习记录与录音保存在项目 `runtime/`，均不会提交到 Git；备份时保留这两处。
- 仓库仅保留启动、构建、界面、后端和内置学习数据所需文件；`.gitignore` 使用逐文件白名单，新增运行依赖时需同步更新白名单。生成的 App、环境、缓存、测试和开发资料不上传。
- 更新代码前退出 App，在项目目录执行 `git pull`，然后重新运行 `start.command` 或 `start.cmd`。启动脚本会按需重新构建本机 App。
- 移动项目后请重新运行启动入口以更新路径。App 和快捷方式应与项目一起保留。
- 跨系统请重新克隆，不要复制 `.venv`、`.tools` 或已构建的 App。
- Windows 仅准备环境和构建、不打开 App：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_windows.ps1 -SetupOnly`。
- 启动脚本使用项目内的环境，不修改全局 PATH；Windows 执行策略参数仅对当前 PowerShell 进程生效。首次准备失败时查看终端错误，修复后重新运行启动入口。

### Windows 语音与录音

- 日语朗读使用 Windows 桌面语音引擎；若提示无可用日语语音，请在系统语言/语音设置中添加日语语音包，完成后重启应用。可用音色取决于系统安装的桌面语音。
- 跟读录音需要开启「设置 → 隐私和安全性 → 麦克风 → 允许桌面应用访问麦克风」。录音最长 60 秒，保存为 `runtime/speaking-latest.wav`，不会上传。
- 导入的 MP3/M4A/WAV 使用系统解码器播放；不支持的编码会显示错误，可改用 WAV 文件。

## 可以学什么

- **每日课程**：中文讲解、假名、例句、听读与课后练习。
- **即时学习卡**：生成词卡，按记忆情况安排复习。
- **真实对话与语法解码器**：练习情景交流，理解日语句子。
- **语法与 JLPT**：学习 N5–N1 语法，练习官方问题集与 AI 模拟题。
- **沉浸日语与学习进度**：阅读短篇故事，查看进度、参加周测。

## 使用须知

- AI 功能需要联网，相关输入会发送给你配置的服务商，并使用你的 API 额度。每次打开 App 时，首页也会自动生成「今日的一点日语」。AI 内容可能有误。
- 浏览课程列表不会自动生成课程；已保存的课程和词卡可离线复习。
