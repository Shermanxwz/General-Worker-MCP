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
matcher = "^mcp__.*__(enter|exit)_worker_mode\$"
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

replace_block() { # swap the existing managed block for the new one, in place
  if ! grep -qxF "$END_MARK" "$REQ_FILE"; then
    warn "$REQ_FILE has the start marker but no end marker; fix it by hand"
    return 1
  fi
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

install_hooks() {
  mkdir -p -m 0755 "$(dirname "$REQ_FILE")" || return 1
  hooks_block > "$TMP_BLOCK" || return 1
  if [ ! -e "$REQ_FILE" ]; then
    cp "$TMP_BLOCK" "$REQ_FILE" && chmod 0644 "$REQ_FILE" || return 1
    say "created $REQ_FILE with the managed hooks"
  elif grep -qxF "$BEGIN_MARK" "$REQ_FILE"; then
    replace_block
  elif grep -Eq '^[[:space:]]*\[hooks\]|^[[:space:]]*\[\[hooks\.|^[[:space:]]*hooks[[:space:]]*=' "$REQ_FILE"; then
    warn "$REQ_FILE already defines hooks outside the managed block; merge this block by hand:"
    cat "$TMP_BLOCK" >&2
    return 1
  else
    append_block
  fi
}

remove_hooks() {
  if [ ! -f "$REQ_FILE" ] || ! grep -qxF "$BEGIN_MARK" "$REQ_FILE"; then
    say "no managed hooks in $REQ_FILE"
    return 0
  fi
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
  [ -d "$CODEX_HOME" ] || { warn "CODEX_HOME is not a directory: $CODEX_HOME"; return 1; }
  if [ "$(id -u)" = 0 ]; then
    owner=$(ls -ldn "$CODEX_HOME" | awk '{print $3}') || return 1
    if [ "$owner" != 0 ]; then
      command -v sudo >/dev/null 2>&1 || { warn "sudo not found: run the codex target as the CODEX_HOME owner"; return 1; }
      sudo -u "#$owner" env CODEX_HOME="$CODEX_HOME" PATH="$PATH" "$CODEX_BIN" mcp "$@"
      return
    fi
  fi
  CODEX_HOME="$CODEX_HOME" "$CODEX_BIN" mcp "$@"
}

mcp_listed() { # 0 when `codex mcp list` mentions worker
  out=$(mcp_run list) || return 1
  printf '%s\n' "$out" | grep -qw worker
}

install_mcp() {
  if mcp_listed && ! mcp_run remove worker >/dev/null; then
    mcp_hint
    return 1
  fi
  if mcp_run add worker -- "$NODE_BIN" "$WORKER_HOME/runtime/worker-mcp.mjs"; then
    say "MCP server worker registered"
  else
    mcp_hint
    return 1
  fi
}

remove_mcp() {
  out=$(mcp_run list) || return 1
  if printf '%s\n' "$out" | grep -qw worker; then
    mcp_run remove worker >/dev/null || return 1
    say "MCP server worker removed"
  else
    say "MCP server worker not listed"
  fi
}
