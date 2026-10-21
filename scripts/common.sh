# Helpers shared by install.sh and the target scripts. Sourced, never run. The sourcing script defines warn,
# which prints its message on stderr with its own prefix.

# path_ok <name> <value>: NODE_BIN and WORKER_HOME end up inside hook and MCP commands, so each must be an
# absolute path of ASCII letters, digits and . _ / + - only (no spaces, quotes, backslashes, $ or control
# characters). Returns 1 and names the variable when it is not.
path_ok() {
  if ! (
    LC_ALL=C # the character ranges below must mean ASCII only
    case $2 in /?*) ;; *) exit 1 ;; esac
    case $2 in *[!A-Za-z0-9._/+-]*) exit 1 ;; esac
  ); then
    warn "$1 must be an absolute path of letters, digits and . _ / + - only: $2"
    return 1
  fi
}
