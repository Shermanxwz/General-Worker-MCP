#!/bin/sh
# Codex target of the General-Worker-MCP installer, run by install.sh:
#   sh scripts/codex.sh <install|uninstall|status>
# Needs REPO_DIR and NODE_BIN (absolute paths) in the environment.
# Helpers: scripts/common.sh (path check), scripts/codex-files.sh (files, skill), scripts/codex-config.sh (hooks, MCP).
set -eu

usage() {
  printf 'usage: sh scripts/codex.sh <install|uninstall|status>\n' >&2
  exit 2
}

[ $# -eq 1 ] || usage
case "$1" in
  install|uninstall|status) ACTION=$1 ;;
  *) usage ;;
esac

if [ -z "${REPO_DIR:-}" ] || [ -z "${NODE_BIN:-}" ]; then
  printf 'codex: REPO_DIR and NODE_BIN must be set (run through install.sh)\n' >&2
  exit 1
fi

CODEX_BIN=${CODEX_BIN:-codex}
CODEX_HOME=${CODEX_HOME:-}
WORKER_HOME=${WORKER_HOME:-/opt/general-worker-mcp}
CODEX_SKILLS_DIR=${CODEX_SKILLS_DIR:-/etc/codex/skills}
REQ_FILE=${CODEX_REQUIREMENTS_FILE:-/etc/codex/requirements.toml}
RUNTIME_SRC=$REPO_DIR/plugins/worker/runtime
RULES_SRC=$REPO_DIR/plugins/worker/rules
SKILL_SRC=$REPO_DIR/codex/skills/worker/SKILL.md
SKILL_DST=$CODEX_SKILLS_DIR/worker/SKILL.md
SKILL_RECORD=$SKILL_DST.installed
TMP_BLOCK=$(mktemp)
TMP_OUT=$(mktemp)
trap 'rm -f "$TMP_BLOCK" "$TMP_OUT"' EXIT

. "$REPO_DIR/scripts/common.sh"
. "$REPO_DIR/scripts/codex-files.sh"
. "$REPO_DIR/scripts/codex-config.sh"

path_ok WORKER_HOME "$WORKER_HOME" && path_ok NODE_BIN "$NODE_BIN" || exit 1

install_all() {
  requirements_path_note
  # The runtime comes first: unless both entry files are in place, nothing else is written.
  install_runtime || return 1
  runtime_ready || return 1
  bad=0
  install_skill || bad=1
  install_hooks || bad=1
  install_mcp || bad=1
  if [ "$bad" -eq 0 ]; then
    say "next: in each codex web collection, check 'worker' in its MCP servers dialog"
    say "next: refresh codex web or start a new thread"
  fi
  return $bad
}

uninstall_all() {
  bad=0
  remove_hooks || bad=1
  remove_mcp || bad=1
  remove_skill || bad=1
  remove_files || bad=1
  return $bad
}

status_all() {
  bad=0
  for n in $(list_names "$RUNTIME_SRC" mjs); do
    report "runtime/$n" "$(file_state "$RUNTIME_SRC/$n" "$WORKER_HOME/runtime/$n")"
  done
  for n in $(list_names "$RULES_SRC" md); do
    report "rules/$n" "$(file_state "$RULES_SRC/$n" "$WORKER_HOME/rules/$n")"
  done
  report "skill" "$(file_state "$SKILL_SRC" "$SKILL_DST")"
  requirements_path_note
  report "requirements block" "$(block_state)"
  rc=0
  mcp_listed || rc=$?
  case $rc in
    0) say "MCP server worker: listed" ;;
    1) report "MCP server worker" "not listed" ;;
    *) report "MCP server worker" "could not list" ;;
  esac
  return $bad
}

case "$ACTION" in
  install) install_all ;;
  uninstall) uninstall_all ;;
  status) status_all ;;
esac
