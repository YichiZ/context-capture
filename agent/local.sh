#!/bin/sh
# Run the agent on the host, through the capture proxy on port 8080.
set -eu
cd "$(dirname "$0")/.."

fail() {
    echo "error: $1" >&2
    exit 1
}

[ -f .env ] || fail ".env not found; see README.md"
# .env is a dotenv file, not shell, so read only the key and never source it.
key=$(sed -n 's/^ANTHROPIC_API_KEY=//p' .env | head -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")
[ -n "$key" ] || fail "put ANTHROPIC_API_KEY in .env"

# The key travels over plain HTTP, so send it only to our own proxy.
proxy_pid=$(lsof -tnP -iTCP:8080 -sTCP:LISTEN 2>/dev/null | head -n 1 || true)
[ -n "$proxy_pid" ] || fail "nothing on port 8080; run: python3 capture.py proxy"
case "$(ps -p "$proxy_pid" -o args= 2>/dev/null || true)" in
    *mitmdump*capture.py*) ;;
    *) fail "port 8080 isn't the capture proxy; run: python3 capture.py proxy" ;;
esac

# Keep shell settings from rerouting the run, so it matches Docker's explicit setup.
for name in $(env | grep -oE '^(ANTHROPIC|CLAUDE)[A-Za-z0-9_]*' || true); do
    unset "$name"
done

# A folder outside the repo, like Docker's /workspace: no source, no .env, no git context.
workspace="${XDG_CACHE_HOME:-$HOME/.cache}/context-capture/workspace"
mkdir -p -m 700 "$workspace" .claude-local
cp agent/fixture.txt "$workspace/"

export ANTHROPIC_API_KEY="$key"
export ANTHROPIC_BASE_URL="http://localhost:8080"
export CLAUDE_CONFIG_DIR="$PWD/.claude-local"
export AGENT_WORKSPACE="$workspace"
exec uv run --no-project --quiet --with claude-agent-sdk==0.2.176 agent/agent.py "$@"
