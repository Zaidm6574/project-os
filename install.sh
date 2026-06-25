#!/usr/bin/env sh
set -eu

show_usage() {
  printf '%s\n' "Usage: ./install.sh /path/to/target-project [--force] [--check-tools]"
  printf '%s\n' ""
  printf '%s\n' "Examples:"
  printf '%s\n' "  ./install.sh ../my-new-project"
  printf '%s\n' "  ./install.sh . --force"
  printf '%s\n' "  ./install.sh ../my-new-project --check-tools"
}

if [ "${1:-}" = "" ] || [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  show_usage
  exit 0
fi

TARGET=$1
shift
FORCE=0
CHECK_TOOLS=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --force)
      FORCE=1
      ;;
    --check-tools)
      CHECK_TOOLS=1
      ;;
    *)
      printf '%s\n' "Unknown option: $1" >&2
      show_usage >&2
      exit 2
      ;;
  esac
  shift
done

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  printf '%s\n' "Project OS installer needs Python 3, but python3 was not found." >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SETUP_SCRIPT="$SCRIPT_DIR/scripts/setup_project_os.py"
CHECK_SCRIPT="$SCRIPT_DIR/scripts/check_optional_tools.py"

if [ ! -f "$SETUP_SCRIPT" ]; then
  printf '%s\n' "Could not find scripts/setup_project_os.py next to install.sh." >&2
  printf '%s\n' "Run this script from a complete Project OS template checkout." >&2
  exit 1
fi

if [ "$FORCE" = "1" ]; then
  "$PYTHON" "$SETUP_SCRIPT" --target "$TARGET" --force
else
  "$PYTHON" "$SETUP_SCRIPT" --target "$TARGET"
fi

if [ "$CHECK_TOOLS" = "1" ]; then
  if [ ! -f "$CHECK_SCRIPT" ]; then
    printf '%s\n' "Could not find scripts/check_optional_tools.py next to install.sh." >&2
    exit 1
  fi
  "$PYTHON" "$CHECK_SCRIPT" --target "$TARGET"
fi

printf '%s\n' ""
printf '%s\n' "Install path finished."
printf '%s\n' "Open the target project in Codex, Claude, or your AI coding tool, then say:"
printf '%s\n' "  /project <your idea>"
