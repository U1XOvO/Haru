#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$PROJECT_DIR/Haru.app"
BUILD_NEEDED=1
if [ -x "$PROJECT_DIR/.venv/bin/python" ] && \
   "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/app_config.py" check; then
  BUILD_NEEDED=0
fi
bash "$PROJECT_DIR/scripts/build_app.sh" --if-needed

# `open` only activates an already-running app. After a rebuild that would leave
# the old native bridge paired with the new UI files from this checkout.
if [ "$BUILD_NEEDED" -eq 1 ] && [ "$(osascript -e "application \"$APP_DIR\" is running")" = "true" ]; then
  osascript -e "tell application \"$APP_DIR\" to quit"
  for _ in {1..50}; do
    [ "$(osascript -e "application \"$APP_DIR\" is running")" = "false" ] && break
    sleep 0.1
  done
  if [ "$(osascript -e "application \"$APP_DIR\" is running")" = "true" ]; then
    echo '旧版 Haru 仍在退出，请关闭后重新运行 start.command。' >&2
    exit 1
  fi
fi
open "$APP_DIR"
