# context-capture

Records the exact JSON body of each Claude Code `POST /v1/messages` request and
summarizes what's in it. Headers are never saved.

**Captures are sensitive.** They contain your prompts, files and tool results. They're
saved to `captures/`, which is ignored by git and readable only by you.

## Setup

```sh
uv tool install mitmproxy
```

## Use

Start the proxy in one terminal. It listens on `127.0.0.1:8080`; stop it with Ctrl-C.

```sh
python3 capture.py proxy
```

Run Claude through it in another terminal. `--safe-mode` keeps your plugins, hooks and
`CLAUDE.md` out of the run:

```sh
ANTHROPIC_BASE_URL=http://localhost:8080 \
  claude --safe-mode -p "Reply with OK"
```

To capture your real setup instead, leave out `--safe-mode`.

Then look at the results. Token counts are estimates (characters / 4).

```sh
python3 capture.py summary              # one row per capture
python3 capture.py tools 0001.json      # per-tool sizes for one capture
```

## Docker harness

Runs Claude Code's harness in a container, through the proxy. Your local setup isn't
mounted. It uses API billing, not your subscription.

Do this setup once. The containers run as your user, so they can write these folders.
Only the proxy container receives your key. Use an editor to add it, so it stays out of
your shell history. Git ignores `.env`.

```sh
mkdir -p -m 700 captures transcripts
touch .env && chmod 600 .env
printf '\nHOST_UID=%s\nHOST_GID=%s\n' "$(id -u)" "$(id -g)" >> .env
${EDITOR:-vi} .env    # add the line ANTHROPIC_API_KEY=your-key
docker compose build
```

Then run it:

```sh
docker compose run --rm agent "Read fixture.txt and reply with its first word"
docker compose run --rm agent "What did you just tell me?"   # continues the conversation
docker compose run --rm agent                                # interactive; exit to stop
docker compose run --rm agent --new "Start over"             # fresh session
docker compose down
```

Request bodies go to `captures/` and session transcripts to `transcripts/` as JSONL.
Both are sensitive and ignored by git.

## Local mode

Runs the same agent on your machine, which is quicker to try but less isolated. It uses
your `.env` key, and needs `python3 capture.py proxy` running in another terminal.

```sh
agent/local.sh "Read fixture.txt and reply with its first word"
agent/local.sh                  # interactive; exit to stop
agent/local.sh --new "Start over"
```

The script refuses to run unless the capture proxy is on port 8080, because your key
travels to it over plain HTTP. It clears `ANTHROPIC_*` and `CLAUDE*` settings from your
shell, and uses a workspace in `~/.cache/context-capture` holding only the fixture.

Sessions stay in `.claude-local/`, separate from `~/.claude` and from Docker sessions.
There's no network or file sandbox: the agent holds your key, and `Read` can reach
anything your user can, including `.env`. Use Docker for evals.

## Tests

```sh
python3 -m unittest
python3 -m unittest discover -s agent
```
