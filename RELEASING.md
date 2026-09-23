# Haru 安装包与更新发布

用户安装包不依赖 Git、Python、uv 或 Apple 命令行工具。开发者仍可使用源码启动入口。

## 构建与验证

在目标系统执行 `uv sync --locked --group build`，再执行：

```sh
uv run --no-sync python scripts/build_release.py
uv run --no-sync python -m unittest discover -s tests -v
node tests/test_release_ui.js
```

Windows 构建机需要 Inno Setup 6（`ISCC` 可以指定编译器路径）；macOS 构建机需要 Apple 命令行工具。Sparkle 2.10.0、WinSparkle 0.9.4 下载到项目的 `build/release-tools`，不全局安装。安装包位于 `dist/installers`。

默认构建是未配置在线更新的 preview 包。`--channel stable --updates` 构建正式版并接入 stable 更新通道，只需要 `HARU_UPDATE_PUBLIC_KEY`；`--signed` 额外要求操作系统发布者证书与 macOS 公证，省略 `--channel` 时默认 stable。发布通道和发布者签名分别配置，两种通道的更新包都必须通过 Ed25519 验证。未配置发布者证书的安装包可能出现未知发布者或未公证提示，Release 说明必须如实注明。

Windows 支持 Windows 10/11 x64，安装前检测 WebView2，缺失时引导安装微软官方 Runtime。Windows 与 macOS 统一使用 Edge TTS 日语朗读，未缓存内容需要联网。macOS 支持 14+，分别构建 arm64 和 x64，DMG 中的 App 应复制到 Applications 后运行。

```sh
# macOS：隔离数据、移动 App 后，验证真实界面和内置后端
uv run --no-sync python scripts/test_bundle.py build/release/Haru.app --gui
# Windows
uv run --no-sync python scripts/test_bundle.py dist/Haru --gui
uv run --no-sync python scripts/test_windows_install.py
```

这些测试不会调用 AI 或在线语音服务。Windows GUI 自检额外验证共享 Edge TTS 模块与音频设备接口能加载；不等同于在线日语合成、真实扬声器和麦克风验收。

## GitHub Actions

工作流 `Desktop installers` 在 `windows-2025`、`macos-15`、`macos-15-intel` 上构建和验证。

- `channel=preview, signed=false, updates=false, publish=false`：无需证书或密钥，产出三个可下载的 Actions 构建附件。
- `channel=preview, signed=false, updates=true, publish=true`：发布预发布安装包，更新包由 Ed25519 签名，更新清单进入 preview 通道。
- `channel=stable, signed=false, updates=true, publish=true`：发布无 preview 后缀的正式版，更新清单进入 stable 通道，仍需说明未使用系统发布者证书。
- 推送 `vMAJOR.MINOR.PATCH` 标签：发布 stable 正式版并启用 Ed25519 更新验签。标签必须与 `pyproject.toml` 的版本一致。默认不使用系统发布者证书；配置相应证书 Secrets 和仓库变量 `HARU_PUBLISHER_SIGNED=true` 后，标签发布会额外启用发布者签名与公证。手动触发通过 `signed=true` 启用。

`pyproject.toml` 是版本的唯一来源，版本变化后更新 `uv.lock`。每次向用户发布可更新的新版本都必须递增版本，不能靠重复发布同一版本强制更新。

只有三个平台全部通过检查，才创建 Release 草稿、上传全部安装包、公开 Release，最后部署更新清单。构建附件和 Release 只上传指定安装包及非敏感构建报告。默认依赖锁文件，stable 或发布者签名构建要求干净检出。

在仓库 Pages 设置中选择 **GitHub Actions**。更新清单固定为：

- `https://u1xovo.github.io/Haru/updates/preview/{platform}.xml`
- `https://u1xovo.github.io/Haru/updates/stable/{platform}.xml`

`platform` 为 `windows-x64`、`macos-arm64` 或 `macos-x64`。清单指向 GitHub Release 的具体版本附件。发布时保留另一通道；preview 不会成为 stable 用户的自动更新候选。用户也可以手动下载安装包切换通道。

若安装包已公开但 Pages 部署失败，现有用户继续使用原版本；修复 Pages 部署即可。不要直接覆盖已经公开的同版本安装包，使用新的版本修复发布问题。

## 签名凭据

操作系统签名与更新包 Ed25519 签名相互独立。私钥、证书、密码绝不能提交到 Git 或上传为构建附件。

所有在线更新需要以下 GitHub Actions Secrets：

| Secret | 内容 |
| --- | --- |
| `HARU_UPDATE_PUBLIC_KEY` | Base64 编码的 32 字节 Ed25519 公钥 |
| `HARU_UPDATE_PRIVATE_KEY` | Sparkle/WinSparkle 支持的 Base64 私钥种子，推荐新格式的 32 字节种子 |

可用官方 `winsparkle-tool generate-key --file <私钥文件>`，或 Sparkle 的 `generate_keys` 生成并妥善备份。私钥应加密备份在仓库以外；不要无迁移方案地更换公钥，否则已安装客户端无法验证后续更新。签名脚本同时用构建报告中的公钥独立验证签名，避免配置了不匹配的密钥仍成功发布。

正式 macOS 签名额外需要：

| Secret | 内容 |
| --- | --- |
| `MACOS_CERTIFICATE_P12` | Developer ID Application 证书及私钥的 P12，整体 Base64 编码 |
| `MACOS_CERTIFICATE_PASSWORD` | P12 密码 |
| `MACOS_SIGN_IDENTITY` | Developer ID Application 签名身份 |
| `APPLE_API_KEY_P8` | 用于公证的 App Store Connect API 私钥文本 |
| `APPLE_API_KEY_ID` / `APPLE_API_ISSUER` | 对应 API Key ID 与 Issuer ID |

正式 Windows 签名使用 `WINDOWS_CERTIFICATE_PFX`（Base64）和 `WINDOWS_CERTIFICATE_PASSWORD`。如果签名提供商要求硬件令牌或云签名，需在有访问该凭据能力的受控 runner 上提供相应签名命令；当前托管流程采用可导入的 PFX。本地签名可提供 `HARU_WINDOWS_CERT_THUMBPRINT` 和 `SIGNTOOL`。

CI 将证书导入临时签名环境，结束时清理；安装包中只有更新公钥。程序下载、签名验证和替换由 Sparkle／WinSparkle 完成，不自行实现下载后覆盖正在运行的可执行文件。

## 数据、迁移与恢复

正式安装包和测试安装包默认共用同一用户数据位置；不要同时运行两个版本：

- Windows：`%LOCALAPPDATA%\Haru`；程序安装到 `%LOCALAPPDATA%\Programs\Haru\versions\版本`。
- macOS：`~/Library/Application Support/Haru`。
- 源码启动：配置保存在项目 `.llm-providers.json`，学习记录保存在 `runtime/`。

`HARU_STORAGE_DIR` 用于整个配置与存储根目录；`HARU_DATA_DIR` 只覆盖学习数据目录，常用于测试。不要把它们指向 App 或安装程序目录。

在安装包的偏好设置中选择“从旧版导入”，先关闭旧版，再选择旧仓库根目录。只向空白安装导入 `.llm-providers.json`、数据库、录音、学习附件、导出和历史备份；旧 `.env` 不会读取或导入。数据库通过 SQLite 在线备份读取，包括 WAL 中已提交的内容；拒绝符号链接、未来数据库版本和覆盖已有学习数据。迁移失败或中断会通过日志恢复，原目录保持不变。

更新安装前检查录音、正在生成的内容、未结束的考试与待保存操作，并在 `upgrade-backups` 中保留数据库和 `.llm-providers.json` 快照，不备份旧 `.env`。快照含个人数据与密钥，只留在本机。Windows 按版本目录安装，失败的新文件不会直接覆盖旧版本目录；卸载默认保留用户数据。

恢复旧程序前必须检查数据库版本。程序拒绝打开未来版本数据库，不会自动把新数据降级覆盖。需要恢复备份时，应先另存当前数据，再由用户明确选择恢复时间点；不能静默丢弃更新后的学习记录。

## 发布前实机验收

自动化验证覆盖：目录迁移、SQLite/WAL 快照、导入中断恢复、不覆盖数据、未来数据库拒绝、更新签名篡改、忙碌状态、移动后的冻结后端与原生 IPC、Windows 安装/修复/卸载。

公开发布前仍需用两个递增版本检查真实更新下载与安装、网络中断、取消、空间不足、错误签名和数据保留；分别在 Windows 和 macOS 实机测试日语朗读、录音、回放、计时考试及正在生成内容时推迟安装。平台签名与公证必须用真实证书验收，模拟测试不能替代。
