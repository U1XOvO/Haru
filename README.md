# Haru · 日语，在日常里

面向中文母语学习者的日语学习 App，用中文讲解、日语听读和日常练习，从零基础逐步入门。

## 下载安装

前往 [GitHub Releases](https://github.com/U1XOvO/Haru/releases)，在版本页面的 **Assets** 中下载安装包：

| 你的设备 | 选择的安装包 |
| --- | --- |
| Mac · Apple Silicon（M 系列），macOS 14+ | `Haru-版本号-macos-arm64.dmg` |
| Mac · Intel，macOS 14+ | `Haru-版本号-macos-x64.dmg` |
| Windows 10 / 11 · x64 | `Haru-版本号-windows-x64-setup.exe` |

- **macOS**：打开 DMG，将 Haru 拖入 Applications（应用程序），再打开 Haru。
- **Windows**：运行安装程序，按提示完成安装；如提示缺少 WebView2，请先安装后重试。

安装包尚未使用系统发布者证书签名与 macOS 公证，首次安装可能出现安全提示或被系统阻止；详见版本页面说明。

## 开始学习

1. 打开「偏好设置 → AI 连接」，填写 API 地址、密钥和模型 ID，选择默认配置，点击「保存并测试」。服务需兼容 Chat Completions 并支持 JSON 输出。
   可添加多个服务商或模型，在「模型参数」中设置思考等级、温度和输出上限等。
2. 进入「每日课程」，选择第一课，点击「AI 创建课程」。
3. 朗读默认使用 Edge TTS。如需 Gemini 3.8 Flash TTS，在「偏好设置 → 朗读与声音」填写自己的 Google AI Studio API Key，选择 Gemini 并保存。两者朗读未缓存的内容都需联网；Gemini 会消耗 Google API 额度，失败时会尝试 Edge TTS。

## 可以学什么

- **每日课程**：假名、词汇、例句、听读与课后练习。
- **即时学习卡**：创建词卡，安排复习。
- **语法解码器**：逐成分理解日语句子、助词与词序。
- **语法、JLPT 与沉浸阅读**：学习 N5–N1 语法，练习试题，阅读日语故事。

## 使用须知

- AI 功能需要联网，相关输入会发送给你配置的服务商，并消耗你的 API 额度；生成内容可能有误。
- 已保存的课程和词卡可离线复习；跟读录音保存在本机，首次录音请允许麦克风访问。
- 在「偏好设置 → 版本与更新」检查更新，也可从 Releases 下载安装新版。
- 学习数据保存在本机，更新应用会保留。备份目录：macOS 为 `~/Library/Application Support/Haru`，Windows 为 `%LOCALAPPDATA%\Haru`。
