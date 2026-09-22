#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
bash "$PROJECT_DIR/scripts/uv.sh" sync --locked
"$PROJECT_DIR/.venv/bin/python" -I -c 'import sys, ssl, sqlite3; assert sys.version_info[:2] == (3, 12); print("项目 Python 环境已就绪：", sys.version.split()[0])'
"$PROJECT_DIR/.venv/bin/python" -I "$PROJECT_DIR/scripts/init_env.py"
