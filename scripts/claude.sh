#!/bin/sh
# Claude Code target of install.sh: marketplace, plugin and v1 skill cleanup.
#
#   sh scripts/claude.sh install|uninstall|status
#
# Needs REPO_DIR and NODE_BIN from install.sh. Optional environment (defaults in parentheses):
#   CLAUDE_BIN                 claude CLI (claude)
#   WORKER_MARKETPLACE_SOURCE  marketplace source: a folder, or owner/repo ($REPO_DIR)
#   CLAUDE_SKILLS_DIR          skills dir for the v1 cleanup (${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills)
set -eu

MARKET=general-worker-mcp
PLUGIN=worker@general-worker-mcp
V1_MARKER='has("agent_id")'

# Reads `plugin marketplace list --json` on stdin. Prints "<source> <location>" for the marketplace named
# argv[1] (its folder for a directory source, else repo or url, with @ref), or nothing when it is not added.
MARKET_JS='
const fs = require("fs");
let list;
try { list = JSON.parse(fs.readFileSync(0, "utf8")); } catch (e) { process.exit(1); }
if (!Array.isArray(list)) process.exit(1);
const m = list.find((e) => e && e.name === process.argv[1]);
if (m) {
  const at = m.ref ? "@" + m.ref : "";
  const where = m.source === "directory" ? m.path : (m.repo || m.url || "") + at;
  process.stdout.write(m.source + " " + where + "\n");
}
'

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

# plugin_listing: prints `plugin list`; on failure reports the last lines of the CLI output and fails.
plugin_listing() {
  if out=$("$CLAUDE_BIN" plugin list 2>&1); then
    printf '%s\n' "$out"
  else
    fail "plugin list"
    printf '%s\n' "$out" | tail -n 3 >&2
    return 1
  fi
}

# need_cli: the CLAUDE_BIN command must exist; checked first so that a wrong path is named in the message.
need_cli() {
  command -v "$CLAUDE_BIN" >/dev/null 2>&1 && return 0
  fail "CLAUDE_BIN is not a command: $CLAUDE_BIN (install the claude CLI or set CLAUDE_BIN)"
  return 1
}

# market_record: prints "<source> <location>" of the general-worker-mcp marketplace, nothing when it is not
# added. Status 1 when the listing cannot be read.
market_record() {
  out=$("$CLAUDE_BIN" plugin marketplace list --json 2>/dev/null) || return 1
  printf '%s\n' "$out" | "$NODE_BIN" -e "$MARKET_JS" "$MARKET"
}

# local_dir <source>: the absolute folder a local source names, without trailing slash, the form the CLI
# records. Status 1 for a remote source such as owner/repo.
local_dir() {
  p=$1
  case $p in
    "~") p=$HOME ;;
    "~"/*) p=$HOME/${p#"~/"} ;;
  esac
  if [ -d "$p" ]; then
    (CDPATH='' cd -- "$p" && pwd)
  else
    case $p in
      /* | ./* | ../*) printf '%s\n' "${p%/}" ;;
      *) return 1 ;;
    esac
  fi
}

has_manifest() { [ -f "$1/.claude-plugin/marketplace.json" ]; }

# run_cli <done> <action> <args...>: runs one claude command. Prints <done> on success; on failure prints
# "failed: <action>" and the last lines of its output.
run_cli() {
  done_label=$1
  action=$2
  shift 2
  if out=$("$CLAUDE_BIN" "$@" 2>&1); then
    say "$done_label"
  else
    fail "$action"
    printf '%s\n' "$out" | tail -n 3 >&2
    return 1
  fi
}

install_marketplace() {
  if want=$(local_dir "$WORKER_MARKETPLACE_SOURCE"); then
    has_manifest "$want" || { fail "marketplace source $want has no .claude-plugin/marketplace.json"; return 1; }
  else
    want=$WORKER_MARKETPLACE_SOURCE
  fi
  rec=$(market_record) || { fail "marketplace list"; return 1; }
  if [ -z "$rec" ]; then
    run_cli "marketplace added ($WORKER_MARKETPLACE_SOURCE)" "marketplace add ($WORKER_MARKETPLACE_SOURCE)" \
      plugin marketplace add "$WORKER_MARKETPLACE_SOURCE"
  elif [ "${rec#* }" = "$want" ]; then
    run_cli "marketplace updated ($MARKET)" "marketplace update ($MARKET)" plugin marketplace update "$MARKET"
  else
    # The CLI keeps one source per marketplace name: remove it (its plugin goes too) and add it again.
    say "marketplace source changed from ${rec#* } to $want"
    run_cli "marketplace removed ($MARKET)" "marketplace remove ($MARKET)" plugin marketplace remove "$MARKET" \
      || return 1
    run_cli "marketplace added ($WORKER_MARKETPLACE_SOURCE)" "marketplace add ($WORKER_MARKETPLACE_SOURCE)" \
      plugin marketplace add "$WORKER_MARKETPLACE_SOURCE"
  fi
}

install_plugin() {
  out=$(plugin_listing) || return 1
  if has "$out" "$PLUGIN"; then
    run_cli "plugin updated ($PLUGIN)" "plugin update ($PLUGIN)" plugin update "$PLUGIN" --scope user
  else
    run_cli "plugin installed ($PLUGIN, user scope)" "plugin install ($PLUGIN)" plugin install "$PLUGIN" --scope user
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
  out=$(plugin_listing) || return 1
  if has "$out" "$PLUGIN"; then
    run_cli "plugin uninstalled ($PLUGIN)" "plugin uninstall ($PLUGIN)" plugin uninstall "$PLUGIN" --scope user || rc=1
  else
    say "plugin not installed, nothing to uninstall"
  fi
  rec=$(market_record) || { fail "marketplace list"; return 1; }
  if [ -n "$rec" ]; then
    run_cli "marketplace removed ($MARKET)" "marketplace remove ($MARKET)" plugin marketplace remove "$MARKET" || rc=1
  else
    say "marketplace not added, nothing to remove"
  fi
  return "$rc"
}

# check_marketplace: the marketplace must be added from WORKER_MARKETPLACE_SOURCE, and a folder source must
# still hold its manifest (a moved or edited checkout fails here).
check_marketplace() {
  rec=$(market_record) || { fail "marketplace list"; return 1; }
  if [ -z "$rec" ]; then
    say "marketplace not added ($MARKET)"
    return 1
  fi
  kind=${rec%% *}
  where=${rec#* }
  want=$(local_dir "$WORKER_MARKETPLACE_SOURCE" 2>/dev/null) || want=$WORKER_MARKETPLACE_SOURCE
  rc=0
  if [ "$where" != "$want" ]; then
    say "marketplace added from $where, not WORKER_MARKETPLACE_SOURCE ($want); run install to switch"
    rc=1
  fi
  if [ "$kind" = directory ] && ! has_manifest "$where"; then
    say "marketplace folder has no .claude-plugin/marketplace.json: $where"
    rc=1
  fi
  if [ "$rc" -eq 0 ]; then say "marketplace added ($where)"; fi
  return "$rc"
}

# cmd_status: exit 0 only when the marketplace and plugin are in place and no worker skill file remains.
cmd_status() {
  rc=0
  check_marketplace || rc=1
  out=$(plugin_listing) || return 1
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

need_cli || exit 1
rc=0
case $ACTION in
  install) cmd_install || rc=1 ;;
  uninstall) cmd_uninstall || rc=1 ;;
  status) cmd_status || rc=1 ;;
esac
exit "$rc"
