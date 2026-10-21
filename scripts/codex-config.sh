# Codex target helpers: the managed hooks block in CODEX_REQUIREMENTS_FILE and the `worker` MCP entry.
# Sourced by scripts/codex.sh, which sets the variables used here.

BEGIN_MARK='# >>> general-worker-mcp (managed by install.sh)'
END_MARK='# <<< general-worker-mcp'

hooks_block() { # prints the managed block
  cat <<EOF
$BEGIN_MARK
[hooks]
managed_dir = "$WORKER_HOME"
[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "$NODE_BIN $WORKER_HOME/runtime/worker-hook.mjs --runtime codex"
timeout = 10
[[hooks.SessionStart]]
[[hooks.SessionStart.hooks]]
type = "command"
command = "$NODE_BIN $WORKER_HOME/runtime/worker-hook.mjs --runtime codex"
timeout = 10
[[hooks.PostToolUse]]
matcher = "^mcp__worker__(enter|exit)_worker_mode\$"
[[hooks.PostToolUse.hooks]]
type = "command"
command = "$NODE_BIN $WORKER_HOME/runtime/worker-hook.mjs --runtime codex"
timeout = 10
$END_MARK
EOF
}

block_state() { # managed block in REQ_FILE -> current | differs | missing
  if [ ! -f "$REQ_FILE" ] || ! grep -qxF "$BEGIN_MARK" "$REQ_FILE"; then
    printf 'missing'
    return 0
  fi
  awk -v b="$BEGIN_MARK" -v e="$END_MARK" \
    '$0 == b { on = 1 } on { print } $0 == e { on = 0 }' "$REQ_FILE" > "$TMP_OUT" || return 1
  hooks_block > "$TMP_BLOCK" || return 1
  if cmp -s "$TMP_OUT" "$TMP_BLOCK"; then
    printf 'current'
  else
    printf 'differs'
  fi
}

# end_marker_present: an edit of the block needs its end marker, or the edit could cut into the content after it.
end_marker_present() {
  grep -qxF "$END_MARK" "$REQ_FILE" && return 0
  warn "$REQ_FILE has the start marker but no end marker; fix it by hand (nothing changed)"
  return 1
}

replace_block() { # swap the existing managed block for the new one, in place
  end_marker_present || return 1
  awk -v blk="$TMP_BLOCK" -v b="$BEGIN_MARK" -v e="$END_MARK" '
    $0 == b { skip = 1; while ((getline line < blk) > 0) print line; next }
    skip { if ($0 == e) skip = 0; next }
    { print }
  ' "$REQ_FILE" > "$TMP_OUT" || return 1
  if cmp -s "$TMP_OUT" "$REQ_FILE"; then
    say "managed hooks already up to date"
  else
    cat "$TMP_OUT" > "$REQ_FILE" || return 1
    say "replaced managed hooks in $REQ_FILE"
  fi
}

append_block() { # block after a blank line; the existing bytes stay as they are
  if [ -s "$REQ_FILE" ]; then
    [ -z "$(tail -c 1 "$REQ_FILE")" ] || printf '\n' >> "$REQ_FILE" || return 1
    printf '\n' >> "$REQ_FILE" || return 1
  fi
  cat "$TMP_BLOCK" >> "$REQ_FILE" || return 1
  say "appended managed hooks to $REQ_FILE"
}

# hooks_defined_outside: true when REQ_FILE defines hooks outside the managed block. Counted: a [hooks] table, a
# [hooks.*] or [[hooks.*]] header, and a hooks key or dotted key before the first table header (the root).
# A hooks key inside another table, such as [features] hooks = true, is not counted. A line that starts with [
# is taken as a table header.
hooks_defined_outside() {
  awk -v b="$BEGIN_MARK" -v e="$END_MARK" '
    BEGIN { root = 1 }
    $0 == b { skip = 1; next }
    skip { if ($0 == e) skip = 0; next }
    /^[[:space:]]*\[/ {
      root = 0
      if ($0 ~ /^[[:space:]]*\[\[?[[:space:]]*"?hooks"?[[:space:]]*[].]/) found = 1
      next
    }
    root && /^[[:space:]]*"?hooks"?[[:space:]]*[=.]/ { found = 1 }
    END { exit (found ? 0 : 1) }
  ' "$REQ_FILE"
}

# requirements_path_note: Codex reads only the default requirements file, so any other path is for testing only.
requirements_path_note() {
  if [ "$REQ_FILE" != /etc/codex/requirements.toml ]; then
    warn "CODEX_REQUIREMENTS_FILE is $REQ_FILE, but Codex reads only /etc/codex/requirements.toml; this path is for testing only"
  fi
}

install_hooks() {
  make_dir "$(dirname "$REQ_FILE")" || return 1
  hooks_block > "$TMP_BLOCK" || return 1
  if [ ! -e "$REQ_FILE" ]; then
    cp "$TMP_BLOCK" "$REQ_FILE" && chmod 0644 "$REQ_FILE" || return 1
    say "created $REQ_FILE with the managed hooks"
    return 0
  fi
  if hooks_defined_outside; then
    warn "$REQ_FILE already defines hooks outside the managed block; merge this block by hand:"
    cat "$TMP_BLOCK" >&2
    return 1
  fi
  if grep -qxF "$BEGIN_MARK" "$REQ_FILE"; then
    replace_block
  else
    append_block
  fi
}

remove_hooks() {
  if [ ! -f "$REQ_FILE" ] || ! grep -qxF "$BEGIN_MARK" "$REQ_FILE"; then
    say "no managed hooks in $REQ_FILE"
    return 0
  fi
  end_marker_present || return 1
  awk -v b="$BEGIN_MARK" -v e="$END_MARK" '
    $0 == b { skip = 1; held = 0; next }
    skip { if ($0 == e) skip = 0; next }
    $0 == "" { if (held) print ""; held = 1; next }
    { if (held) print ""; held = 0; print }
    END { if (held) print "" }
  ' "$REQ_FILE" > "$TMP_OUT" || return 1
  if grep -q '[^[:space:]]' "$TMP_OUT"; then
    cat "$TMP_OUT" > "$REQ_FILE" || return 1
    say "removed managed hooks from $REQ_FILE (other content kept)"
  else
    rm -f "$REQ_FILE" || return 1
    say "removed $REQ_FILE (it held only the managed hooks)"
  fi
}

mcp_hint() {
  warn "MCP step failed; add this to ${CODEX_HOME:-<CODEX_HOME>}/config.toml by hand:"
  printf '[mcp_servers.worker]\ncommand = "%s"\nargs = ["%s"]\n' \
    "$NODE_BIN" "$WORKER_HOME/runtime/worker-mcp.mjs" >&2
}

mcp_run() { # codex mcp <args>; run as the CODEX_HOME owner when we are root
  if [ -z "$CODEX_HOME" ]; then
    warn "CODEX_HOME is not set, so '$CODEX_BIN mcp' cannot run; set it to the codex web owner's Codex home"
    return 1
  fi
  command -v "$CODEX_BIN" >/dev/null 2>&1 || { warn "CODEX_BIN is not a command: $CODEX_BIN"; return 1; }
  [ -d "$CODEX_HOME" ] || { warn "CODEX_HOME is not a directory: $CODEX_HOME"; return 1; }
  if [ "$(id -u)" = 0 ]; then
    # Only the numeric owner column is read below; no file name is parsed, so the ls warning does not apply.
    # shellcheck disable=SC2012
    owner=$(ls -ldn "$CODEX_HOME" | awk '{print $3}') || return 1
    if [ "$owner" != 0 ]; then
      command -v sudo >/dev/null 2>&1 || { warn "sudo not found: run the codex target as the CODEX_HOME owner"; return 1; }
      sudo -u "#$owner" env CODEX_HOME="$CODEX_HOME" PATH="$PATH" "$CODEX_BIN" mcp "$@"
      return
    fi
  fi
  CODEX_HOME="$CODEX_HOME" "$CODEX_BIN" mcp "$@"
}

mcp_names() { # prints the name of each server in `codex mcp list --json`, one per line
  out=$(mcp_run list --json) || return 1
  printf '%s\n' "$out" | "$NODE_BIN" -e '
    const list = JSON.parse(require("fs").readFileSync(0, "utf8"));
    for (const server of list) console.log(server.name);
  '
}

mcp_listed() { # 0 when a server is named exactly worker; 1 when none is; 2 when the list cannot be read
  names=$(mcp_names) || return 2
  printf '%s\n' "$names" | grep -qx worker
}

install_mcp() {
  rc=0
  mcp_listed || rc=$?
  case $rc in
    0)
      mcp_run remove worker >/dev/null || { mcp_hint; return 1; } ;;
    1) ;;
    *)
      # Without the list we cannot tell whether worker is registered, so nothing is added.
      warn "could not read the MCP server list, so worker was not registered"
      mcp_hint
      return 1 ;;
  esac
  if mcp_run add worker -- "$NODE_BIN" "$WORKER_HOME/runtime/worker-mcp.mjs"; then
    say "MCP server worker registered"
  else
    mcp_hint
    return 1
  fi
}

remove_mcp() {
  rc=0
  mcp_listed || rc=$?
  case $rc in
    0)
      mcp_run remove worker >/dev/null || { warn "could not remove the worker MCP server"; return 1; }
      say "MCP server worker removed" ;;
    1) say "MCP server worker not listed" ;;
    *) warn "could not read the MCP server list, so worker was not removed"; return 1 ;;
  esac
}
