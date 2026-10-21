# Codex target helpers: messages, directories, runtime and rules copies, and the skill.
# Sourced by scripts/codex.sh, which sets the variables used here.

say() { printf 'codex: %s\n' "$*"; }
warn() { printf 'codex: %s\n' "$*" >&2; }

# make_dir <dir>: creates the directory and any missing parents. The umask is 022 for this step only, so every
# directory it creates is 0755, the parents included. An existing directory keeps its mode.
make_dir() {
  [ -d "$1" ] || (umask 022 && mkdir -p -- "$1")
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

copy_one() { # src dst: copy with mode 0644; a directory in the way is an error, not a place for the copy
  [ -f "$1" ] || { warn "missing source file $1"; return 1; }
  if [ -d "$2" ]; then
    warn "cannot copy $1: $2 is a directory"
    return 1
  fi
  cp "$1" "$2" && chmod 0644 "$2"
}

# runtime_ready: the hooks and the MCP server run from these two files, so both must be in place before either
# is registered.
runtime_ready() {
  for n in worker-hook.mjs worker-mcp.mjs; do
    if [ ! -f "$WORKER_HOME/runtime/$n" ]; then
      warn "$WORKER_HOME/runtime/$n is missing; the hooks and the MCP server were not written"
      return 1
    fi
  done
}

install_runtime() {
  if [ ! -d "$RUNTIME_SRC" ] || [ ! -d "$RULES_SRC" ]; then
    warn "runtime or rules directory missing under $REPO_DIR"
    return 1
  fi
  make_dir "$WORKER_HOME/runtime" && make_dir "$WORKER_HOME/rules" || return 1
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

# skill_is_ours: true when SKILL_DST is still the copy this installer last wrote, which SKILL_RECORD holds.
skill_is_ours() {
  [ -f "$SKILL_DST" ] && [ -f "$SKILL_RECORD" ] && cmp -s "$SKILL_DST" "$SKILL_RECORD"
}

# keep_previous_skill: runs before SKILL_DST is replaced. A file that is not this installer's copy is saved as
# SKILL_DST.bak, but only when no backup exists yet, so the first original is the one that is kept.
keep_previous_skill() {
  [ -f "$SKILL_DST" ] || return 0
  if skill_is_ours; then
    return 0
  fi
  if [ -e "$SKILL_DST.bak" ]; then
    warn "$SKILL_DST is not the worker skill and is replaced; the first original stays in $SKILL_DST.bak"
    return 0
  fi
  cp "$SKILL_DST" "$SKILL_DST.bak" || return 1
  say "previous skill saved as $SKILL_DST.bak; uninstall restores it"
}

install_skill() {
  make_dir "$(dirname "$SKILL_DST")" || return 1
  if [ -f "$SKILL_DST" ] && cmp -s "$SKILL_SRC" "$SKILL_DST"; then
    say "skill already up to date"
  else
    keep_previous_skill || return 1
    cp "$SKILL_SRC" "$SKILL_DST" && chmod 0644 "$SKILL_DST" || return 1
    say "skill installed at $SKILL_DST"
  fi
  cp "$SKILL_SRC" "$SKILL_RECORD" && chmod 0644 "$SKILL_RECORD" || return 1
}

# remove_skill: restores the backup when there is one; otherwise removes the skill only when it is still ours.
remove_skill() {
  if [ -e "$SKILL_DST.bak" ]; then
    mv -f "$SKILL_DST.bak" "$SKILL_DST" || return 1
    say "restored the skill that was there before the install: $SKILL_DST"
  elif skill_is_ours; then
    rm -f "$SKILL_DST" || return 1
    say "skill file removed: $SKILL_DST"
  elif [ -f "$SKILL_DST" ]; then
    warn "$SKILL_DST is not the worker skill; left in place"
  else
    say "no worker skill file to remove"
  fi
  rm -f "$SKILL_RECORD" || return 1
  rmdir "$(dirname "$SKILL_DST")" 2>/dev/null || true
}
