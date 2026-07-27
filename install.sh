#!/usr/bin/env sh
set -eu

show_usage() {
  printf '%s\n' "Usage: ./install.sh /path/to/target-project [--force] [--dry-run] [--check-tools] [--allow-unsafe-target] [--full-engine] [--claude-engine] [--codex-engine] [--central-brain PATH] [--project-id ID]"
  printf '%s\n' ""
  printf '%s\n' "The target must be a project folder. \$HOME, /, and system directories are"
  printf '%s\n' "refused (an install writes ~120 files and appends to the target's .gitignore);"
  printf '%s\n' "pass --allow-unsafe-target to override. Symlinks inside the target are never"
  printf '%s\n' "written through - they are reported and skipped."
  printf '%s\n' ""
  printf '%s\n' "Examples:"
  printf '%s\n' "  ./install.sh ../my-new-project"
  printf '%s\n' "  ./install.sh ../my-new-project --dry-run"
  printf '%s\n' "  ./install.sh . --force"
  printf '%s\n' "  ./install.sh ../my-new-project --check-tools"
  printf '%s\n' "  ./install.sh ../my-new-project --full-engine --claude-engine"
  printf '%s\n' "  ./install.sh ../my-new-project --full-engine --codex-engine"
  printf '%s\n' "  ./install.sh ../my-new-project --full-engine --central-brain ~/.project-os/central-brain --project-id my-project"
}

if [ "${1:-}" = "" ] || [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  show_usage
  exit 0
fi

TARGET=$1
shift
FORCE=0
DRY_RUN=0
CHECK_TOOLS=0
ALLOW_UNSAFE_TARGET=0
FULL_ENGINE=0
CLAUDE_ENGINE=0
CODEX_ENGINE=0
CENTRAL_BRAIN=""
PROJECT_ID=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --force)
      FORCE=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    --check-tools)
      CHECK_TOOLS=1
      ;;
    --allow-unsafe-target)
      ALLOW_UNSAFE_TARGET=1
      ;;
    --full-engine)
      FULL_ENGINE=1
      ;;
    --claude-engine)
      FULL_ENGINE=1
      CLAUDE_ENGINE=1
      ;;
    --codex-engine)
      FULL_ENGINE=1
      CODEX_ENGINE=1
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

PYTHON=""
FOUND_OLD=""
# Try versioned binaries too: on stock macOS `python3` is 3.9 while a newer
# interpreter is often installed under a versioned name (external review
# finding, 2026-07-17 — probing only python3/python failed on default PATH).
for candidate in python3 python python3.13 python3.12 python3.11 python3.10 python3.14; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PYTHON=$candidate
    break
  fi
  # remember the first too-old interpreter so the failure names what it found
  if [ "$FOUND_OLD" = "" ]; then
    found_version=$("$candidate" -c 'import platform; print(platform.python_version())' 2>/dev/null) || found_version="unknown version"
    FOUND_OLD="$candidate = ${found_version:-unknown version} ($(command -v "$candidate"))"
  fi
done

if [ "$PYTHON" = "" ]; then
  if [ "$FOUND_OLD" != "" ]; then
    printf '%s\n' "found $FOUND_OLD; Project OS installer needs Python 3.10 or newer." >&2
  else
    printf '%s\n' "Project OS installer needs Python 3.10 or newer, but no compatible Python command was found." >&2
  fi
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

set -- --target "$TARGET"
if [ "$FORCE" = "1" ]; then
  set -- "$@" --force
fi
if [ "$DRY_RUN" = "1" ]; then
  set -- "$@" --dry-run
fi
if [ "$ALLOW_UNSAFE_TARGET" = "1" ]; then
  set -- "$@" --allow-unsafe-target
fi
"$PYTHON" "$SETUP_SCRIPT" "$@"

if [ "$FULL_ENGINE" = "1" ]; then
  if [ ! -f "$FULL_ENGINE_SCRIPT" ]; then
    printf '%s\n' "Could not find scripts/install_full_engine.py next to install.sh." >&2
    exit 1
  fi
  set -- --target "$TARGET"
  if [ "$FORCE" = "1" ]; then
    set -- "$@" --force
  fi
  if [ "$DRY_RUN" = "1" ]; then
    set -- "$@" --dry-run --starter-planned
  fi
  if [ "$CLAUDE_ENGINE" = "1" ]; then
    set -- "$@" --claude
  fi
  if [ "$CODEX_ENGINE" = "1" ]; then
    set -- "$@" --codex
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
  if [ "$DRY_RUN" = "1" ]; then
    printf '%s\n' "Dry run: would run optional tool check for $TARGET"
    printf '%s\n' ""
  else
    if [ ! -f "$CHECK_SCRIPT" ]; then
      printf '%s\n' "Could not find scripts/check_optional_tools.py next to install.sh." >&2
      exit 1
    fi
    "$PYTHON" "$CHECK_SCRIPT" --target "$TARGET"
  fi
fi

printf '%s\n' ""
printf '%s\n' "Install path finished."
printf '%s\n' "Open the target project in Codex, Claude, or your AI coding tool, then say:"
printf '%s\n' "  /project <your idea>"
