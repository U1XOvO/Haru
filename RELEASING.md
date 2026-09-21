# 构建与发布 Haru

`pyproject.toml` 的 `project.version` 是唯一发布版本源，格式为 `MAJOR.MINOR.PATCH`。Windows 文件版本、macOS Info.plist、归档文件名和构建清单都读取它。`uv.lock` 固定运行与构建依赖。

## GitHub Release

1. 更新 `pyproject.toml` 中的版本、`RELEASE_NOTES.md` 中的版本说明，并更新 `uv.lock`。提交代码，包括 `.github/workflows/release.yml`、`tests/` 和所有构建脚本。
2. 推送与版本完全相同的标签，例如版本为 `1.0.0` 时使用 `v1.0.0`。已有标签不要覆盖，应提升版本。
3. GitHub Actions 分别在 Windows x64、macOS arm64、macOS Intel 上运行离线测试，构建独立 App，再将 App 移到临时目录验证原生界面、后台 IPC、资源与持久化。
4. 三个平台全部通过后，工作流创建 **Draft Release** 并上传三个 ZIP。到仓库 Releases 页面检查安装包和说明，再点击 Publish release。任一构建或测试失败都不会创建发布草稿。

普通分支推送、Pull Request 和手动运行工作流只构建和上传 Actions artifacts，不创建 Release。已发布的 Release 不会被重跑覆盖；草稿允许重新上传。

当前使用 GitHub 提供的标准托管 runner。macOS arm64 使用 `macos-15`，Intel 使用 `macos-15-intel`；见 [GitHub runner 文档](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。工作流不需要第三方服务密钥，只有发布任务获得仓库内容写权限。

## 本地构建

必须在目标系统与架构上构建；PyInstaller 不进行跨系统编译。

macOS：

```sh
bash scripts/uv.sh sync --locked --group build
.venv/bin/python scripts/build_release.py build
.venv/bin/python scripts/test_macos_bundle.py
.venv/bin/python scripts/build_release.py package
```

Windows PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/uv.ps1 sync --locked --group build
.venv\Scripts\python.exe scripts/build_release.py build
.venv\Scripts\python.exe scripts/test_windows_bundle.py
.venv\Scripts\python.exe scripts/build_release.py package
```

打包结果位于 `dist/releases/`。macOS 使用 ZIP 保留 `.app`、执行权限与运行库符号链接；Windows ZIP 包含完整的应用文件夹。每个 App 带有 `release-info.json`，记录版本、平台、Python、依赖与签名状态，并保留已安装依赖提供的许可文件。

自动验证使用临时学习目录和占位配置，不请求真实 AI。源码的 `.env`、`runtime/`、`.venv` 和缓存不属于发布输入；打包阶段也会拒绝包含 `.env` 或学习数据库的归档。

## 签名与已有数据

当前流程不使用发布者证书：Windows 未签名，macOS 为 ad-hoc 签名并执行 `codesign --verify --deep --strict`，尚未公证。它们可以作为 GitHub Release 附件，但仍可能触发系统来源提示。正式证书签名和 Apple 公证须另行配置证书与开发者账户；不要把密钥放进仓库。

源码启动与 Release 使用不同的数据位置，迁移须在 App 关闭时按 README 进行。Release 更新只替换 App，不操作用户目录。打包工具参考 [PyInstaller 文档](https://pyinstaller.org/en/stable/)。
