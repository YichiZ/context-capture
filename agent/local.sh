#!/bin/sh
# Run the agent on the host, through the proxy on port 8080.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "error: .env not found; see README.md" >&2; exit 1; }
set -a
. ./.env
set +a
: "${ANTHROPIC_API_KEY:?put ANTHROPIC_API_KEY in .env}"
export CLAUDE_CONFIG_DIR="$PWD/.claude-local"
export ANTHROPIC_BASE_URL="http://localhost:8080"
export AGENT_WORKSPACE="$PWD/agent"
mkdir -p -m 700 "$CLAUDE_CONFIG_DIR"
exec uv run --no-project --quiet --with claude-agent-sdk==0.2.176 agent/agent.py "$@"
