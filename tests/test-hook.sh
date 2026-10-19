#!/bin/sh
# Tests the PreToolUse hook and matcher in claude/skills/worker/SKILL.md, plus the
# frontmatter of both skills. The matcher and the hook command are pulled out of
# SKILL.md with grep/sed, so no YAML parser is needed. The hook itself needs jq,
# and so does this script.
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CLAUDE_SKILL=$ROOT/claude/skills/worker/SKILL.md
CODEX_SKILL=$ROOT/codex/skills/worker/SKILL.md

pass=0
fail=0
report() { # report ok|bad <text>
  if [ "$1" = ok ]; then
    pass=$((pass + 1)); echo "PASS: $2"
  else
    fail=$((fail + 1)); echo "FAIL: $2"
  fi
}
check() { # check <label> <command...>: PASS when the command succeeds
  label=$1; shift
  if "$@" >/dev/null 2>&1; then report ok "$label"; else report bad "$label"; fi
}

for f in "$CLAUDE_SKILL" "$CODEX_SKILL"; do
  if [ ! -f "$f" ]; then
    echo "FAIL: missing $f"
    exit 1
  fi
done

MATCHER=$(sed -n 's/^[[:space:]]*- matcher: "\(.*\)"[[:space:]]*$/\1/p' "$CLAUDE_SKILL")
HOOK_CMD=$(grep 'jq -e' "$CLAUDE_SKILL" | sed 's/^[[:space:]]*//')
HOOK_LINES=$(grep -c 'jq -e' "$CLAUDE_SKILL" || true)

if [ -n "$MATCHER" ]; then report ok "found hook matcher in SKILL.md"; else report bad "found hook matcher in SKILL.md"; fi
if [ "$HOOK_LINES" = 1 ]; then report ok "found exactly one hook command (line with jq -e)"; else report bad "found exactly one hook command (line with jq -e), got $HOOK_LINES"; fi

# run_hook <json>: runs the hook command the way the harness does (JSON on stdin).
# Sets OUT (stdout) and RC (exit status).
run_hook() {
  if [ -z "$HOOK_CMD" ]; then
    OUT=""; RC=99
    return
  fi
  OUT=$(printf '%s' "$1" | sh -c "$HOOK_CMD" 2>/dev/null)
  RC=$?
}

DENY_JQ='.hookSpecificOutput.permissionDecision == "deny"'

run_hook '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls"}}'
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | jq -e "$DENY_JQ" >/dev/null 2>&1; then
  report ok "main thread Bash is denied (permissionDecision deny, exit 0)"
else
  report bad "main thread Bash is denied (exit $RC, stdout: $OUT)"
fi

run_hook '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls"},"agent_id":"a1","agent_type":"general-purpose"}'
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
  report ok "subagent Bash (has agent_id) passes silently (empty stdout, exit 0)"
else
  report bad "subagent Bash (has agent_id) passes silently (exit $RC, stdout: $OUT)"
fi

run_hook 'not json'
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | jq -e "$DENY_JQ" >/dev/null 2>&1; then
  report ok "malformed input is denied (fails closed)"
else
  report bad "malformed input is denied (exit $RC, stdout: $OUT)"
fi

for n in Edit Write NotebookEdit Bash Monitor mcp__github__push_files mcp__workspace__bash; do
  if printf '%s\n' "$n" | grep -E "$MATCHER" >/dev/null 2>&1; then
    report ok "matcher matches $n"
  else
    report bad "matcher matches $n"
  fi
done

for n in Read Agent SendMessage TaskStop ToolSearch Skill AskUserQuestion WebFetch WebSearch ReadMcpResourceTool ListMcpResourcesTool; do
  if printf '%s\n' "$n" | grep -E "$MATCHER" >/dev/null 2>&1; then
    report bad "matcher does not match $n"
  else
    report ok "matcher does not match $n"
  fi
done

check "claude SKILL.md has name: worker" grep -q '^name: worker$' "$CLAUDE_SKILL"
check "claude SKILL.md has disable-model-invocation: true" grep -q '^disable-model-invocation: true$' "$CLAUDE_SKILL"
check "codex SKILL.md has name: worker" grep -q '^name: worker$' "$CODEX_SKILL"
check "codex SKILL.md has a description: line" grep -q '^description: ' "$CODEX_SKILL"

echo "$pass passed, $fail failed"
if [ "$fail" -eq 0 ]; then
  exit 0
fi
exit 1
