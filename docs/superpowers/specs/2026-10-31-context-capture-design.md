# context-capture design

**Status:** draft for review. Not committed, per the "don't commit or push" constraint.
**Date:** 2026-10-31

## Why

Yichi wants to see the exact context Claude Code sends to the model on each request:
harness instructions, tool schemas, conversation history and tool results.

This is also the first step toward a later undo/redo context harness. Each saved
request body is a full snapshot of the context at that moment, so captures are the
raw material that harness will step through.

## Goals

- Save every `POST /v1/messages` request body as a numbered, pretty-printed JSON file.
- Summarize each capture: size by section, model, message count and tool count.
- Show a per-tool size breakdown for one capture.
- Prove it works with a real request from an isolated Claude run.
- Then move the agent into a Docker container built on the Claude Agent SDK.

## Non-goals

- Undo/redo itself, including editing or replaying requests. This is a later project.
- Capturing model responses. The next request contains prior replies, so only the
  last reply of a session is missed.
- A web UI, a database, or diffing between captures.
- Real tokenizing. Token counts are characters / 4 and are labeled as estimates.

## Anti-goals

The project has failed, even if it works, if any of these happen:

- A header, API key or OAuth token is saved to a capture, a repo file or an image, or printed.
- Full request contents appear in tool output or reports.
- Captures become committable to git.
- `~/.claude/` config or the shell profile is deliberately edited.
- An auth or proxy failure is worked around instead of shown to Yichi.

## Constraints

- Python standard library only, apart from mitmproxy. Phase 2 adds the Agent SDK,
  inside the container only.
- Don't commit or push.
- Don't print full request contents.
- `captures/` is listed in `.gitignore`.

## Phase 1: local capture

### Layout

Files live in the repo root, because the repo is already named `context-capture`.
Yichi chose to combine the three scripts and the two test files, replacing the original
file names, to keep the project small.

| File | Purpose |
|---|---|
| `capture.py` | mitmproxy addon, plus the `proxy`, `summary` and `tools` commands |
| `test_capture.py` | `unittest` tests |
| `README.md` | Exact commands |
| `.gitignore` | Contains `captures/` and `__pycache__/` |

### Flow

```text
claude -> 127.0.0.1:8080 (mitmdump + addon) -> https://api.anthropic.com
                |
                v
        captures/0001.json
```

### Capturing (`capture.py`)

- Matches `POST` requests whose path, without the query string, is exactly
  `/v1/messages`. Claude Code may add `?beta=true`, and this rule also excludes
  `/v1/messages/count_tokens`.
- Saves the decoded body only, never headers.
- Numbers files `0001.json` upward, continuing from the highest existing number.
- Creates files as `0600` and `captures/` as `0700`.
- Logs and skips a body that isn't JSON, without printing it. The request is still
  forwarded.
- Streams responses straight through, by setting `flow.response.stream = True` in the
  `responseheaders` hook, so Claude's output isn't held until the response ends.
- Imports nothing from mitmproxy, so tests run without it installed.

### `python3 capture.py proxy`

Runs the installed `mitmdump`. Yichi chose a real install over `uvx`, and it was
installed with `uv tool install mitmproxy`:

```sh
mitmdump \
  --mode reverse:https://api.anthropic.com \
  --listen-host 127.0.0.1 --listen-port 8080 \
  -s capture.py
```

mitmproxy creates `~/.mitmproxy` on first run.

### Summarizing (`capture.py`)

- `python3 capture.py summary` prints one row per capture: file, model, messages, tools,
  and estimated tokens for `system`, `tools` and `messages`.
- `python3 capture.py tools 0001.json` prints each tool's estimated tokens,
  largest first.
- Sizes come from each section's compact JSON serialization.
- Output states that tokens are estimates (characters / 4).
- Output never includes message text.
- A bad capture shows an error row and doesn't stop the others.

### Isolated verification run

Yichi chose safe mode. The Phase 1 test used two flags, shown here as run. Yichi later
dropped `--no-session-persistence`, so the README no longer uses it:

```sh
ANTHROPIC_BASE_URL=http://localhost:8080 \
  claude --safe-mode --no-session-persistence -p "Reply with OK"
```

- `--safe-mode` turns off plugins, hooks, MCP servers, skills and `CLAUDE.md`.
- `--no-session-persistence` stops the session transcript being saved.

This isolates what Claude loads, not what it can touch. Claude still runs as Yichi
and may update its own state, such as `~/.claude.json`. Any such change is reported.

Whether safe mode still uses the existing keychain login is untested. If login or the
proxy fails, stop and show the error. Don't switch to an API key without Yichi's say-so.

### Done when

1. Unit tests pass.
2. A real run produces at least one capture.
3. `capture.py summary` prints a row for it, and the report includes that output.
4. A search of `captures/` finds no auth header values.
5. The proxy is stopped.

## Phase 2: Docker with the Agent SDK

Yichi signed off on planning this. Yichi accepted API billing, and the key goes in `.env`. The container gives a harder boundary than safe mode.

### What the SDK docs establish

- `claude-agent-sdk` for Python bundles a native Claude Code binary, pinned to the
  package version. It requires Python 3.10+. The docs don't confirm an arm64 Linux
  wheel, so that's the first thing to check on this Mac.
- Auth is an API key. Anthropic does not allow third-party developers to offer
  claude.ai login, so this can't use Yichi's subscription login.
- `ANTHROPIC_BASE_URL` is documented for routing the SDK through a proxy. The docs also
  describe a proxy that injects the key outside the container.
- The SDK's default system prompt is minimal and differs from `claude -p`. The real one
  needs `system_prompt={"type": "preset", "preset": "claude_code"}`.
- `setting_sources=[]` turns off user, project and local settings.
- `allowed_tools` auto-approves tools but doesn't restrict them. Restricting the tool
  set needs a separate check, because the docs don't say whether `tools=[...]` limits it.
- Transcripts are written under `CLAUDE_CONFIG_DIR/projects/` and are lost with the
  container unless that folder is mounted.

### Design

Two services in `docker-compose.yml`:

```text
agent container --(internal network only)--> proxy container --> api.anthropic.com
       |                                          |
       v                                          v
./transcripts/*.jsonl                      ./captures/*.json
```

- **`proxy`:** runs mitmdump with `capture.py`, joined to the internal network and one
  with internet access. It listens on all interfaces inside the container, because
  `127.0.0.1` isn't reachable from another container. It publishes no host ports.
- **`agent`:** a small Python script calling `query()` with `setting_sources=[]`, the
  `claude_code` preset, `ANTHROPIC_BASE_URL=http://proxy:8080`, and the `Read` tool. It
  joins the internal network only, so the proxy is its only way out.
- **API key:** Yichi keeps `ANTHROPIC_API_KEY` in `.env`, which git ignores. Compose
  passes it to `proxy` only, and `capture.py` sets the `x-api-key` header when
  `UPSTREAM_API_KEY` is set. `agent` gets a placeholder, so a compromised agent has no
  key to leak. This is the proxy pattern the docs describe.
- **Continuous conversation:** Yichi wants one ongoing conversation with the harness, so
  sessions persist. With a prompt, `agent.py` runs one turn. Without one, it opens an
  interactive loop. Either way it continues the latest session in `./transcripts`, and
  `--new` starts a fresh one. A new container can pick up where the last one stopped.
- **Hardening:** `agent` runs as a non-root user with a read-only root filesystem, all
  capabilities dropped, `no-new-privileges`, and a 1 GiB memory and 1 CPU limit.
- **Transcripts:** `CLAUDE_CONFIG_DIR=/data/claude`, with `./transcripts` mounted at
  `/data/claude/projects`. This is the only writable mount on `agent`. `transcripts/`
  is ignored by git. This keeps both views: JSONL for what the agent recorded, and
  captures for what was really sent.
- **Mounts:** nothing from `~/.claude/` or the home directory goes into either container.

### Phase 2 done when

1. `docker compose run --rm agent "Read fixture.txt and reply with its first word"`
   prints the fixture's first word.
2. New files appear in `./captures` and `./transcripts`, and the host commands read them.
3. Captures contain the Claude Code preset prompt and a `tool_result` block.
4. Captures show none of Yichi's `CLAUDE.md` or plugin content.
5. The agent container cannot reach `api.anthropic.com` directly.
6. The real key is absent from the agent's environment, captures and transcripts.
7. A second `docker compose run` remembers the first, and its capture has more messages.

## Left to the builder

- Exact table formatting and how the README is worded.
- Agent script argument handling and the base image, within the Python 3.10+ requirement.
- Internal function names in `capture.py`.
- The mitmproxy version, unless the default fails.

## Calls made without Yichi's explicit say-so

- `.gitignore` also ignores `__pycache__/`.
- Bodies are written with standard ASCII-escaped JSON, so unusual Unicode can't fail a
  save. Non-English text appears as `\uXXXX` in capture files.
- Phase 2 uses Python rather than TypeScript for the SDK, to match the repo.
- Phase 2 uses a separate proxy container on an internal network.
- Phase 2 injects the key at the proxy, and keeps JSONL transcripts in `./transcripts`.
- Phase 2 uses the `claude_code` prompt preset and the `Read` tool, because the goal is
  seeing the real harness context and tool results.
- Moving to Phase 2 needs Yichi's sign-off, even if Phase 1 passes every check.
- Phase 2 keeps `capture.py` unchanged and reuses it.
- A later non-isolated capture, for comparing against the real setup, is left to the
  harness project.
