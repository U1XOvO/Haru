#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ICONSET="$PROJECT_DIR/build/Haru.iconset"
mkdir -p "$ICONSET" "$PROJECT_DIR/build/swift-cache" "$PROJECT_DIR/assets"

# One drawing supplies the sidebar, browser favicon and all macOS icon sizes.
xcrun swift -module-cache-path "$PROJECT_DIR/build/swift-cache" "$PROJECT_DIR/scripts/make_icon.swift" "$PROJECT_DIR/assets/icon.png"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$PROJECT_DIR/assets/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  doubled=$((size * 2))
  sips -z "$doubled" "$doubled" "$PROJECT_DIR/assets/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
if iconutil -c icns "$ICONSET" -o "$PROJECT_DIR/assets/Haru.icns"; then
  cp "$ICONSET/icon_128x128.png" "$PROJECT_DIR/ui/brand-icon.png"
elif [ -s "$PROJECT_DIR/assets/Haru.icns" ] && [ -s "$PROJECT_DIR/ui/brand-icon.png" ]; then
  echo 'iconutil 未接受新图标集，保留现有的 Haru 图标继续本机构建。' >&2
else
  echo '无法生成或复用 Haru 图标。' >&2
  exit 1
fi
