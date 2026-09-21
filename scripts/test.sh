#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
cd "$PROJECT_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo '请先运行 ./start.command 准备项目 Python 环境。' >&2
  exit 1
fi

"$PYTHON_BIN" -B - <<'PY'
import ast
from pathlib import Path

project = Path.cwd()
count = 0
for folder in ('backend', 'scripts', 'tests', 'native'):
    for source_file in sorted((project / folder).rglob('*.py')):
        ast.parse(source_file.read_text(encoding='utf-8'), filename=str(source_file))
        count += 1
print(f'Python syntax passed: {count} files')
ast.parse((project / 'start.py').read_text(encoding='utf-8'))
PY

for source_file in "$PROJECT_DIR"/start.command "$PROJECT_DIR"/scripts/*.sh; do
  bash -n "$source_file"
done

if command -v node >/dev/null 2>&1; then
  for source_file in "$PROJECT_DIR"/ui/*.js "$PROJECT_DIR"/tests/*.cjs; do
    node --check "$source_file"
  done
else
  echo '未找到 Node.js；跳过 JavaScript 语法检查。'
fi

export PYTHONDONTWRITEBYTECODE=1
"$PYTHON_BIN" -B -m unittest discover -s "$PROJECT_DIR/tests" -v
