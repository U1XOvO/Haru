#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
bash "$PROJECT_DIR/scripts/build_app.sh" --if-needed
open "$PROJECT_DIR/Haru.app"
