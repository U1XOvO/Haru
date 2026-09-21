#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if ! xcrun --find swiftc >/dev/null 2>&1; then
  echo '请先执行 xcode-select --install 安装 Apple 命令行工具，完成后重新运行 start.command。' >&2
  exit 1
fi
bash "$PROJECT_DIR/scripts/setup_env.sh"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
APP_DIR="$PROJECT_DIR/Haru.app"
if [ "${1:-}" = --if-needed ] && "$PYTHON_BIN" "$PROJECT_DIR/scripts/app_config.py" check; then
  echo 'App 已是最新，准备打开。'
  exit 0
fi
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources" "$PROJECT_DIR/build/swift-cache"
"$PYTHON_BIN" "$PROJECT_DIR/scripts/app_config.py" prepare
bash "$PROJECT_DIR/scripts/build_icons.sh"
xcrun swiftc "$PROJECT_DIR/native/Haru.swift" -swift-version 5 -O -module-cache-path "$PROJECT_DIR/build/swift-cache" -framework AppKit -framework WebKit -framework AVFoundation -o "$APP_DIR/Contents/MacOS/Haru"
"$PYTHON_BIN" "$PROJECT_DIR/scripts/app_config.py" write
cp "$PROJECT_DIR/assets/Haru.icns" "$APP_DIR/Contents/Resources/Haru.icns"
codesign --force --sign - "$APP_DIR"
touch "$APP_DIR"
echo "构建完成：$APP_DIR"
