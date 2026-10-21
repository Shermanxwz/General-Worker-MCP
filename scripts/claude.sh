#!/bin/sh
# Claude Code target of install.sh: marketplace, plugin and v1 skill cleanup.
#
#   sh scripts/claude.sh install|uninstall|status
#
# Needs REPO_DIR and NODE_BIN from install.sh. Optional environment (defaults in parentheses):
#   CLAUDE_BIN                 claude CLI (claude)
#   WORKER_MARKETPLACE_SOURCE  marketplace source ($REPO_DIR)
#   CLAUDE_SKILLS_DIR          skills dir for the v1 cleanup (${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills)
set -eu

MARKET=general-worker-mcp
PLUGIN=worker@general-worker-mcp
V1_MARKER='has("agent_id")'

say() { printf 'claude: %s\n' "$*"; }
fail() { printf 'claude: failed: %s\n' "$*" >&2; }
warn() { printf 'claude: warning: %s\n' "$*" >&2; }

# has <listing> <name>: true when the CLI listing mentions the name.
has() {
  case $1 in
    *"$2"*) return 0 ;;
  esac
  return 1
}

ACTION=${1-}
case $ACTION in
  install | uninstall | status) ;;
  *) echo "usage: sh scripts/claude.sh install|uninstall|status" >&2; exit 2 ;;
esac
if [ -z "${REPO_DIR:-}" ] || [ -z "${NODE_BIN:-}" ]; then
  echo "claude: REPO_DIR and NODE_BIN must be set; run install.sh instead" >&2
  exit 1
fi
CLAUDE_BIN=${CLAUDE_BIN:-claude}
WORKER_MARKETPLACE_SOURCE=${WORKER_MARKETPLACE_SOURCE:-$REPO_DIR}
CLAUDE_SKILLS_DIR=${CLAUDE_SKILLS_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills}
V1_DIR=$CLAUDE_SKILLS_DIR/worker
V1_FILE=$V1_DIR/SKILL.md

list_plugins() { "$CLAUDE_BIN" plugin list 2>&1; }
list_markets() { "$CLAUDE_BIN" plugin marketplace list 2>&1; }

# run_cli <label> <args...>: runs one claude command and prints one line for the step.
run_cli() {
  label=$1
  shift
  if out=$("$CLAUDE_BIN" "$@" 2>&1); then
    say "$label"
  else
    fail "$label"
    printf '%s\n' "$out" | tail -n 3 >&2
    return 1
  fi
}

install_marketplace() {
  out=$(list_markets) || { fail "marketplace list"; return 1; }
  if has "$out" "$MARKET"; then
    run_cli "marketplace updated ($MARKET)" plugin marketplace update "$MARKET"
  else
    run_cli "marketplace added ($WORKER_MARKETPLACE_SOURCE)" plugin marketplace add "$WORKER_MARKETPLACE_SOURCE"
  fi
}

install_plugin() {
  out=$(list_plugins) || { fail "plugin list"; return 1; }
  if has "$out" "$PLUGIN"; then
    run_cli "plugin updated ($PLUGIN)" plugin update "$PLUGIN" --scope user
  else
    run_cli "plugin installed ($PLUGIN, user scope)" plugin install "$PLUGIN" --scope user
  fi
}

# cleanup_v1: removes the v1 skill (marker match) with its .bak and an empty dir; warns about any other file.
cleanup_v1() {
  [ -f "$V1_FILE" ] || return 0
  if ! grep -Fq "$V1_MARKER" "$V1_FILE"; then
    warn "$V1_FILE is not the v1 worker skill; left in place"
    return 0
  fi
  if ! rm -f "$V1_FILE" "$V1_FILE.bak"; then
    fail "could not remove v1 skill $V1_FILE"
    return 1
  fi
  rmdir "$V1_DIR" 2>/dev/null || true
  say "removed v1 skill $V1_FILE"
}

cmd_install() {
  rc=0
  install_marketplace || rc=1
  if [ "$rc" -eq 0 ]; then install_plugin || rc=1; fi
  cleanup_v1 || rc=1
  return "$rc"
}

cmd_uninstall() {
  rc=0
  out=$(list_plugins) || { fail "plugin list"; return 1; }
  if has "$out" "$PLUGIN"; then
    run_cli "plugin uninstalled ($PLUGIN)" plugin uninstall "$PLUGIN" --scope user || rc=1
  else
    say "plugin not installed, nothing to uninstall"
  fi
  out=$(list_markets) || { fail "marketplace list"; return 1; }
  if has "$out" "$MARKET"; then
    run_cli "marketplace removed ($MARKET)" plugin marketplace remove "$MARKET" || rc=1
  else
    say "marketplace not added, nothing to remove"
  fi
  return "$rc"
}

# cmd_status: exit 0 only when the plugin is listed and no worker skill file remains.
cmd_status() {
  rc=0
  out=$(list_plugins) || { fail "plugin list"; return 1; }
  if has "$out" "$PLUGIN"; then
    say "plugin installed ($PLUGIN)"
  else
    say "plugin not installed ($PLUGIN)"
    rc=1
  fi
  if [ ! -f "$V1_FILE" ]; then
    say "no v1 skill"
  elif grep -Fq "$V1_MARKER" "$V1_FILE"; then
    say "v1 skill still present ($V1_FILE); run install to remove it"
    rc=1
  else
    warn "non-v1 skill at $V1_FILE, left alone"
    rc=1
  fi
  return "$rc"
}

rc=0
case $ACTION in
  install) cmd_install || rc=1 ;;
  uninstall) cmd_uninstall || rc=1 ;;
  status) cmd_status || rc=1 ;;
esac
exit "$rc"
