#!/usr/bin/env bash
# Load Skillenai API credentials into the current shell.
# Sourced, not executed — callers do: source scripts/load_env.sh
#
# Precedence:
#   1. API_KEY already set in the shell environment
#   2. ~/.skillenai/.env                (user-scoped; survives plugin upgrades)
#   3. $CLAUDE_PLUGIN_ROOT/.env         (contributor-local, for in-tree editing)
#   4. ./.env                           (cwd fallback)

_skn_source_env_file() {
  local f="$1"
  [ -f "$f" ] || return 1
  set -a
  # shellcheck disable=SC1090
  . "$f"
  set +a
  return 0
}

if [ -z "${API_KEY:-}" ]; then
  _skn_source_env_file "$HOME/.skillenai/.env" \
    || { [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && _skn_source_env_file "$CLAUDE_PLUGIN_ROOT/.env"; } \
    || _skn_source_env_file "./.env" \
    || true
fi

: "${API_URL:=https://api.skillenai.com}"
: "${APP_URL:=https://app.skillenai.com/api/backend}"
export API_URL APP_URL
[ -n "${API_KEY:-}" ] && export API_KEY

unset -f _skn_source_env_file

if [ -z "${API_KEY:-}" ]; then
  echo "[skillenai] warning: API_KEY is not set. Put your key in ~/.skillenai/.env or export API_KEY in your shell." >&2
fi
