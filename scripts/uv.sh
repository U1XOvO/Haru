#!/bin/bash
# Project-local uv entry point; does not change the user's shell or global tools.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV_VERSION="0.12.15"
UV_BIN="$PROJECT_DIR/.tools/uv/uv"
if [ "$(uname -s)" != Darwin ]; then
  echo 'Haru 的原生 App 需要 macOS 14 或以上。' >&2
  exit 1
fi
if [ ! -x "$UV_BIN" ] || [[ "$("$UV_BIN" --version)" != "uv $UV_VERSION"* ]]; then
  echo "首次准备：安装项目专用 uv ${UV_VERSION}（需要联网）…" >&2
  mkdir -p "$PROJECT_DIR/.tools/uv"
  installer="$(mktemp "${TMPDIR:-/tmp}/haru-uv-installer.XXXXXX")"
  trap 'rm -f "$installer"' EXIT
  curl --fail --location --silent --show-error --connect-timeout 15 --max-time 120 --retry 2 \
    "https://astral.sh/uv/$UV_VERSION/install.sh" -o "$installer"
  UV_UNMANAGED_INSTALL="$PROJECT_DIR/.tools/uv" sh "$installer"
  rm -f "$installer"
  trap - EXIT
fi
export UV_CACHE_DIR="$PROJECT_DIR/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PROJECT_DIR/.tools/python"
export UV_PYTHON_INSTALL_BIN=0
export UV_PROJECT_ENVIRONMENT="$PROJECT_DIR/.venv"
export UV_PYTHON_PREFERENCE=only-managed
export UV_HTTP_TIMEOUT=60
export UV_HTTP_RETRIES=2
unset VIRTUAL_ENV PYTHONHOME PYTHONPATH
exec "$UV_BIN" --directory "$PROJECT_DIR" "$@"
