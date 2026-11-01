# Local mode plan

**Goal:** run `agent/agent.py` on the host as well as in Docker. Docker stays the reference
for evals.

## Changes

- `agent/agent.py`: read `AGENT_WORKSPACE` and `CLAUDE_CONFIG_DIR` at call time. Defaults stay
  `/workspace` and `/data/claude`, so Docker doesn't change.
- `agent/local.sh`: loads `.env`, sets `CLAUDE_CONFIG_DIR=$PWD/.claude-local`,
  `ANTHROPIC_BASE_URL=http://localhost:8080` and `AGENT_WORKSPACE=$PWD/agent`, then runs
  `agent.py` with `uv run --no-project --with claude-agent-sdk==0.2.176`.
- `.gitignore`: add `.claude-local/`.
- `README.md`: add local mode and its weaker isolation.

## Tests

- `build_options` uses env overrides, and keeps Docker defaults when unset.
- The continue guard looks under `CLAUDE_CONFIG_DIR/projects`.

## Verify

1. Both unit test suites pass.
2. Through the proxy on port 8080, a local run reads the fixture and returns `pineapple`.
3. A second local run continues the conversation.
4. New captures appear, and the key isn't in captures or `.claude-local/`.
5. No session is written to `~/.claude/projects` for the agent workspace.
6. The Docker flow still works.
