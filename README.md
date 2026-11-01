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

## Tests

```sh
python3 -m unittest
```
