#!/usr/bin/env sh
set -eu

show_usage() {
  printf '%s\n' "Usage: ./install.sh /path/to/target-project [--force] [--check-tools] [--full-engine] [--claude-engine] [--central-brain PATH] [--project-id ID]"
  printf '%s\n' ""
  printf '%s\n' "Examples:"
  printf '%s\n' "  ./install.sh ../my-new-project"
  printf '%s\n' "  ./install.sh . --force"
  printf '%s\n' "  ./install.sh ../my-new-project --check-tools"
  printf '%s\n' "  ./install.sh ../my-new-project --full-engine --claude-engine"
  printf '%s\n' "  ./install.sh ../my-new-project --full-engine --central-brain ~/.project-os/central-brain --project-id my-project"
}

if [ "${1:-}" = "" ] || [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  show_usage
  exit 0
fi

TARGET=$1
shift
FORCE=0
CHECK_TOOLS=0
FULL_ENGINE=0
CLAUDE_ENGINE=0
CENTRAL_BRAIN=""
PROJECT_ID=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --force)
      FORCE=1
      ;;
    --check-tools)
      CHECK_TOOLS=1
      ;;
    --full-engine)
      FULL_ENGINE=1
      ;;
    --claude-engine)
      FULL_ENGINE=1
      CLAUDE_ENGINE=1
      ;;
    --central-brain)
      if [ "${2:-}" = "" ]; then
        printf '%s\n' "--central-brain needs a path argument." >&2
        exit 2
      fi
      FULL_ENGINE=1
      CENTRAL_BRAIN=$2
      shift
      ;;
    --project-id)
      if [ "${2:-}" = "" ]; then
        printf '%s\n' "--project-id needs an id argument." >&2
        exit 2
      fi
      PROJECT_ID=$2
      shift
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
FULL_ENGINE_SCRIPT="$SCRIPT_DIR/scripts/install_full_engine.py"

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

if [ "$FULL_ENGINE" = "1" ]; then
  if [ ! -f "$FULL_ENGINE_SCRIPT" ]; then
    printf '%s\n' "Could not find scripts/install_full_engine.py next to install.sh." >&2
    exit 1
  fi
  set -- --target "$TARGET"
  if [ "$CLAUDE_ENGINE" = "1" ]; then
    set -- "$@" --claude
  fi
  if [ "$CENTRAL_BRAIN" != "" ]; then
    set -- "$@" --central-brain "$CENTRAL_BRAIN"
  fi
  if [ "$PROJECT_ID" != "" ]; then
    set -- "$@" --project-id "$PROJECT_ID"
  fi
  "$PYTHON" "$FULL_ENGINE_SCRIPT" "$@"
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
