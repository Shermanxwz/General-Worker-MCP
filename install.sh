#!/bin/sh
# Install, uninstall or inspect the worker skill for Claude Code and Codex.
#
#   sh install.sh [install|uninstall|status] [all|claude|codex]     default: install all
#
# Skills dirs (environment, with defaults):
#   CLAUDE_SKILLS_DIR  ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills
#   CODEX_SKILLS_DIR   $HOME/.agents/skills
#
# Only <dir>/worker/SKILL.md, <dir>/worker/SKILL.md.bak and an empty <dir>/worker/ are touched.
set -eu

usage() {
  cat <<'EOF'
usage: sh install.sh [install|uninstall|status] [all|claude|codex]
  default: install all
  CLAUDE_SKILLS_DIR  default ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills
  CODEX_SKILLS_DIR   default $HOME/.agents/skills
EOF
}

if [ $# -gt 2 ]; then
  usage >&2
  exit 2
fi
ACTION=${1:-install}
WHICH=${2:-all}
case $ACTION in
  -h|--help) usage; exit 0 ;;
  install|uninstall|status) ;;
  *) usage >&2; exit 2 ;;
esac
case $WHICH in
  all) TARGETS="claude codex" ;;
  claude|codex) TARGETS=$WHICH ;;
  *) usage >&2; exit 2 ;;
esac

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
CLAUDE_CONFIG=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
CLAUDE_SKILLS_DIR=${CLAUDE_SKILLS_DIR:-$CLAUDE_CONFIG/skills}
CODEX_SKILLS_DIR=${CODEX_SKILLS_DIR:-$HOME/.agents/skills}

# set_target <claude|codex>: sets SRC (repo copy), DIR (<skills dir>/worker), DEST (DIR/SKILL.md).
set_target() {
  case $1 in
    claude) SRC=$SCRIPT_DIR/claude/skills/worker/SKILL.md; DIR=$CLAUDE_SKILLS_DIR/worker ;;
    codex)  SRC=$SCRIPT_DIR/codex/skills/worker/SKILL.md;  DIR=$CODEX_SKILLS_DIR/worker ;;
  esac
  DEST=$DIR/SKILL.md
}

need_src() {
  if [ ! -f "$SRC" ]; then
    echo "error: repo file missing: $SRC" >&2
    echo "       run install.sh from inside the General-Worker-MCP checkout." >&2
    exit 1
  fi
}

do_install() {
  set_target "$1"
  need_src
  mkdir -p "$DIR"
  if [ -f "$DEST" ] && ! cmp -s "$SRC" "$DEST"; then
    cp "$DEST" "$DEST.bak"
    echo "  saved the differing file as $DEST.bak"
  fi
  cp "$SRC" "$DEST"
  chmod 0644 "$DEST"
  echo "installed: $DEST"
  case $1 in
    claude) echo "  Claude: new sessions get /worker." ;;
    codex)  echo "  Codex: refresh codex web, or start a new thread, to pick up the skill." ;;
  esac
}

do_uninstall() {
  set_target "$1"
  if [ -f "$DEST" ]; then
    rm -f "$DEST"
    echo "removed: $DEST"
  else
    echo "not installed: $DEST"
  fi
  if [ -f "$DEST.bak" ]; then
    rm -f "$DEST.bak"
    echo "removed: $DEST.bak"
  fi
  if [ -d "$DIR" ] && rmdir "$DIR" 2>/dev/null; then
    echo "removed empty dir: $DIR"
  fi
}

do_status() {
  set_target "$1"
  need_src
  if [ ! -f "$DEST" ]; then
    state="not installed"
  elif cmp -s "$SRC" "$DEST"; then
    state="installed (up to date)"
  else
    state="installed (differs from repo)"
  fi
  echo "$1: $DEST: $state"
}

rc=0
for t in $TARGETS; do
  case $ACTION in
    install)
      if [ "$t" = claude ] && ! command -v jq >/dev/null 2>&1; then
        echo "error: jq is not on PATH, so the Claude worker skill is NOT installed." >&2
        echo "       Its PreToolUse hook calls jq. Without jq every tool call, subagents included, would be denied." >&2
        echo "       Install jq (for example: apt install jq) and run again." >&2
        rc=1
        continue
      fi
      do_install "$t"
      ;;
    uninstall) do_uninstall "$t" ;;
    status) do_status "$t" ;;
  esac
done
exit $rc
