#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)

if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1 && python -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    PYTHON=python
else
    printf '%s\n' 'Python 3.9 or newer is required.' >&2
    exit 2
fi

export PYTHONDONTWRITEBYTECODE=1
cd -- "$PROJECT_ROOT"
exec "$PYTHON" -m skill_orchestrator "$@"
