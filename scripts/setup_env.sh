#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
bash "$PROJECT_DIR/scripts/uv.sh" sync --locked
"$PROJECT_DIR/.venv/bin/python" -I -c 'import sys, ssl, sqlite3; assert sys.version_info[:2] == (3, 12); print("项目 Python 环境已就绪：", sys.version.split()[0])'
"$PROJECT_DIR/.venv/bin/python" -I - "$PROJECT_DIR" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
try:
    with (root / '.env').open('x', encoding='utf-8') as output:
        output.write((root / '.env.example').read_text(encoding='utf-8'))
    print('已创建 .env：可直接使用内置课程；在线 AI 功能需填写 API 配置。')
except FileExistsError:
    pass
PY
