#!/bin/sh
# General-Worker-MCP v2 installer: validates NODE_BIN, then runs the target scripts in scripts/.
#
#   sh install.sh [ACTION] [TARGET]     defaults: install all
#   sh install.sh -h | --help           usage
#
# Exit status: 0 every requested component succeeded, 1 a component failed, 2 usage error.
set -eu

usage() {
  cat <<'EOF'
usage: sh install.sh [ACTION] [TARGET]
       sh install.sh -h | --help

ACTION   install (default) | uninstall | status
TARGET   all (default) | claude | codex

Environment variables (default in parentheses):
  NODE_BIN                   absolute path of node 18 or newer (command -v node); used by all targets
  Claude target:
  CLAUDE_BIN                 claude CLI (claude)
  WORKER_MARKETPLACE_SOURCE  marketplace source (this checkout)
  CLAUDE_CONFIG_DIR          Claude config dir ($HOME/.claude)
  CLAUDE_SKILLS_DIR          skills dir, only for v1 skill cleanup (${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills)
  Codex target:
  CODEX_BIN                  codex CLI (codex)
  CODEX_HOME                 Codex home of the codex web owner (no default; needed by the MCP step)
  WORKER_HOME                runtime install dir (/opt/general-worker-mcp)
  CODEX_SKILLS_DIR           skills dir (/etc/codex/skills)
  CODEX_REQUIREMENTS_FILE    managed requirements file (/etc/codex/requirements.toml); for testing only:
                             Codex reads only /etc/codex/requirements.toml

Exit status: 0 success, 1 a component failed, 2 usage error.
EOF
}

err() { printf 'install.sh: %s\n' "$*" >&2; }

usage_error() {
  err "$1"
  usage >&2
  exit 2
}

ACTION=install
TARGET=all
for arg in "$@"; do
  case $arg in
    -h | --help) usage; exit 0 ;;
  esac
done
[ $# -le 2 ] || usage_error "too many arguments"
[ $# -lt 1 ] || ACTION=$1
[ $# -lt 2 ] || TARGET=$2
case $ACTION in
  install | uninstall | status) ;;
  *) usage_error "unknown action: $ACTION" ;;
esac
case $TARGET in
  all | claude | codex) ;;
  *) usage_error "unknown target: $TARGET" ;;
esac

case $0 in
  */*) dir=${0%/*} ;;
  *) dir=. ;;
esac
REPO_DIR=$(CDPATH= cd "$dir" && pwd) || { err "cannot resolve the checkout directory"; exit 1; }

# abs_path <path>: absolute form of a path, or the PATH lookup of a bare name; prints nothing if none.
abs_path() {
  case $1 in
    /*) printf '%s\n' "$1" ;;
    */*) (CDPATH= cd "${1%/*}" 2>/dev/null && printf '%s/%s\n' "$(pwd)" "${1##*/}") ;;
    *) command -v "$1" 2>/dev/null ;;
  esac
}

# check_node: sets NODE_BIN to a usable absolute node path, or reports why not (return 1).
check_node() {
  n=${NODE_BIN:-$(command -v node 2>/dev/null || true)}
  n=$(abs_path "$n") || n=
  if [ -z "$n" ]; then
    err "NODE_BIN: no node found; set NODE_BIN to an absolute path of node 18 or newer"
    return 1
  fi
  case $n in
    *' '* | *'"'* | *"'"*) err "NODE_BIN must not contain spaces or quotes: $n"; return 1 ;;
  esac
  if [ ! -f "$n" ] || [ ! -x "$n" ]; then
    err "NODE_BIN is not an executable file: $n"
    return 1
  fi
  v=$("$n" --version 2>/dev/null) || v=
  major=${v#v}
  major=${major%%.*}
  case $major in
    '' | *[!0-9]*) err "NODE_BIN $n did not report a version"; return 1 ;;
  esac
  if [ "$major" -lt 18 ]; then
    err "NODE_BIN $n reports $v; node 18 or newer is required"
    return 1
  fi
  NODE_BIN=$n
}

rc=0
if check_node; then
  d=${NODE_BIN%/*}
  [ -n "$d" ] || d=/
  PATH=$d:$PATH
  export PATH NODE_BIN REPO_DIR
  case $TARGET in all | claude) sh "$REPO_DIR/scripts/claude.sh" "$ACTION" || rc=1 ;; esac
  case $TARGET in all | codex) sh "$REPO_DIR/scripts/codex.sh" "$ACTION" || rc=1 ;; esac
else
  rc=1
  case $TARGET in all | claude) err "claude: failed, not run because NODE_BIN is invalid" ;; esac
  case $TARGET in all | codex) err "codex: failed, not run because NODE_BIN is invalid" ;; esac
fi
exit "$rc"
