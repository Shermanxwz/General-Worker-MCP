# Codex target helpers: messages, path checks, runtime/rules copies and the skill.
# Sourced by scripts/codex.sh, which sets the variables used here.

say() { printf 'codex: %s\n' "$*"; }
warn() { printf 'codex: %s\n' "$*" >&2; }

# path_ok <name> <value>: NODE_BIN and WORKER_HOME are written into hook and MCP commands, so each must be an
# absolute path of letters, digits and . _ / + - only (no spaces, quotes, backslashes, $ or control characters).
path_ok() {
  (
    LC_ALL=C # the ranges below must mean ASCII only
    case $2 in /?*) ;; *) exit 1 ;; esac
    case $2 in *[!A-Za-z0-9._/+-]*) exit 1 ;; esac
  ) || { warn "$1 must be an absolute path of letters, digits and . _ / + - only: $2"; return 1; }
}

# make_dir <dir>: creates a missing directory and its parents; the directory itself gets mode 0755.
make_dir() {
  [ -d "$1" ] || { mkdir -p "$1" && chmod 0755 "$1"; }
}

list_names() { # dir ext -> names of the files with that extension
  for f in "$1"/*."$2"; do
    if [ -f "$f" ]; then basename "$f"; fi
  done
}

report() { # label state -> one status line; the run is bad unless the state is current
  say "$1: $2"
  [ "$2" = current ] || bad=1
}

file_state() { # src dst -> current | differs | missing
  if [ ! -f "$2" ]; then
    printf 'missing'
  elif cmp -s "$1" "$2"; then
    printf 'current'
  else
    printf 'differs'
  fi
}

copy_one() { # src dst: copy with mode 0644
  [ -f "$1" ] || { warn "missing source file $1"; return 1; }
  cp "$1" "$2" && chmod 0644 "$2"
}

install_runtime() {
  if [ ! -d "$RUNTIME_SRC" ] || [ ! -d "$RULES_SRC" ]; then
    warn "runtime or rules directory missing under $REPO_DIR"
    return 1
  fi
  make_dir "$WORKER_HOME" && make_dir "$WORKER_HOME/runtime" && make_dir "$WORKER_HOME/rules" || return 1
  bad=0
  for n in $(list_names "$RUNTIME_SRC" mjs); do
    copy_one "$RUNTIME_SRC/$n" "$WORKER_HOME/runtime/$n" || bad=1
  done
  for n in $(list_names "$RULES_SRC" md); do
    copy_one "$RULES_SRC/$n" "$WORKER_HOME/rules/$n" || bad=1
  done
  [ $bad -eq 0 ] && say "runtime and rules copied to $WORKER_HOME"
  return $bad
}

remove_files() {
  for n in $(list_names "$RUNTIME_SRC" mjs); do
    rm -f "$WORKER_HOME/runtime/$n" || return 1
  done
  for n in $(list_names "$RULES_SRC" md); do
    rm -f "$WORKER_HOME/rules/$n" || return 1
  done
  rmdir "$WORKER_HOME/runtime" "$WORKER_HOME/rules" "$WORKER_HOME" 2>/dev/null || true
  say "runtime and rules files removed"
}

install_skill() {
  make_dir "$(dirname "$SKILL_DST")" || return 1
  if [ -f "$SKILL_DST" ] && cmp -s "$SKILL_SRC" "$SKILL_DST"; then
    say "skill already up to date"
    return 0
  fi
  if [ -f "$SKILL_DST" ]; then
    cp "$SKILL_DST" "$SKILL_DST.bak" || return 1
    say "previous skill saved as $SKILL_DST.bak"
  fi
  cp "$SKILL_SRC" "$SKILL_DST" && chmod 0644 "$SKILL_DST" || return 1
  say "skill installed at $SKILL_DST"
}

remove_skill() {
  rm -f "$SKILL_DST" "$SKILL_DST.bak" || return 1
  rmdir "$(dirname "$SKILL_DST")" 2>/dev/null || true
  say "skill files removed"
}
