#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
SMOKE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/cso-smoke.XXXXXXXX")
STATE_ROOT="$SMOKE_ROOT/state"
SKILLS_ROOT="$SMOKE_ROOT/skills"

cleanup() {
    case "$SMOKE_ROOT" in
        "${TMPDIR:-/tmp}"/cso-smoke.*) rm -rf -- "$SMOKE_ROOT" ;;
        *) printf '%s\n' 'Refusing unsafe smoke cleanup.' >&2; exit 5 ;;
    esac
}
trap cleanup EXIT HUP INT TERM

export PYTHONDONTWRITEBYTECODE=1
cd -- "$PROJECT_ROOT"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1 && python -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    PYTHON=python
else
    printf '%s\n' 'Python 3.9 or newer is required.' >&2
    exit 2
fi
"$PYTHON" -m unittest discover -s tests -v

PROJECT_FIXTURE="$SMOKE_ROOT/project"
mkdir -p -- "$PROJECT_FIXTURE"
printf '%s\n' '{"dependencies":{"react":"1"},"devDependencies":{"vitest":"1"}}' > "$PROJECT_FIXTURE/package.json"
./installer/install.sh --help >/dev/null
./installer/install.sh analyze --help >/dev/null
./installer/install.sh init --help >/dev/null
./installer/install.sh doctor --help >/dev/null
./installer/install.sh analyze --project-root "$PROJECT_FIXTURE" >/dev/null
test ! -e "$PROJECT_FIXTURE/.cso"
./installer/install.sh analyze --project-root "$PROJECT_FIXTURE" --json >/dev/null
test ! -e "$PROJECT_FIXTURE/.cso"
./installer/install.sh init --project-root "$PROJECT_FIXTURE" --yes >/dev/null
test -f "$PROJECT_FIXTURE/.cso/config.json"
./installer/install.sh doctor --project-root "$PROJECT_FIXTURE" >/dev/null

rm -rf -- "$SMOKE_ROOT"
./installer/install.sh install --profile universal --install-root "$STATE_ROOT" --skills-dir "$SKILLS_ROOT" --dry-run --json
test ! -e "$SMOKE_ROOT"

./installer/install.sh install --profile universal --install-root "$STATE_ROOT" --skills-dir "$SKILLS_ROOT" --json
./installer/install.sh audit --install-root "$STATE_ROOT" --skills-dir "$SKILLS_ROOT" --json
./installer/install.sh activate --profile economy --install-root "$STATE_ROOT" --skills-dir "$SKILLS_ROOT" --json
./installer/install.sh rollback --install-root "$STATE_ROOT" --skills-dir "$SKILLS_ROOT" --json

printf '%s\n' 'POSIX smoke test passed.'
