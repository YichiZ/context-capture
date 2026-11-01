# context-capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record Claude Code's `/v1/messages` request bodies through a local proxy and summarize what's in them, then repeat the capture from a Docker container running the Agent SDK.

**Architecture:** A mitmproxy addon saves each request body to `captures/`, and a separate stdlib script reads those files. Phase 2 reuses the addon unchanged in a proxy container, which is the agent container's only network path.

**Tech Stack:** Python 3 stdlib, `unittest`, mitmproxy 12.3.1 (installed), and in Phase 2 Docker Compose and `claude-agent-sdk`.

**Layout note:** Everything lives in `capture.py` and `test_capture.py`. Tasks 1 and 2 add to the same two files. The commands are `proxy`, `summary` and `tools FILE`.

**Spec:** `docs/superpowers/specs/2026-10-31-context-capture-design.md`

## Global Constraints

- Don't commit or push. This overrides the usual commit step, so no task has one.
- Python stdlib only, apart from mitmproxy. The Agent SDK is allowed inside the container only.
- Never save or print headers, API keys or OAuth tokens.
- Never print full request contents, including while debugging.
- Don't edit `~/.claude/` config or the shell profile.
- If auth or the proxy fails, stop and show the error. Don't work around it or switch to an API key.
- Phase 2 needs a Claude Console API key and Docker running. The real key goes to the proxy container only.

## Review Focus

1. `system` sent as a plain string rather than a block list: sizes are still reported.
2. A tool with no `name`, such as some server-side tools: it's listed as `?`, with no crash.
3. No captures yet, or a missing `captures/`: a clear message and exit 0, not a traceback.
4. `tools` given a file that's missing or corrupt: a clear error and exit 1.
5. A compressed body that can't be decoded: skipped and logged, while the request is still forwarded.

---

## Phase 1: local capture

### Task 1: `capture.py`

**Files:**
- Create: `capture.py`
- Modify: `test_capture.py` (draft exists)

**Interfaces:**
- Consumes: capture files, each a JSON object.
- Produces:
  - `summarize_capture(path: Path) -> dict` with keys `file`, `model`, `messages`, `tools`, `system_tokens`, `tools_tokens`, `messages_tokens`
  - `tool_sizes(path: Path) -> list[tuple[str, int]]`, largest first
  - Both raise `ValueError` on a bad capture
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Add these tests to `test_capture.py`, alongside the existing ones**

```python
def test_system_as_string(self):
    self.path.write_text(json.dumps({"system": "x" * 400}))
    self.assertGreaterEqual(summarize_capture(self.path)["system_tokens"], 100)

def test_unnamed_tool_is_question_mark(self):
    self.path.write_text(json.dumps({"tools": [{"type": "server"}]}))
    self.assertEqual(tool_sizes(self.path)[0][0], "?")

def test_main_handles_empty_and_missing_dir(self):
    missing = self.path.parent / "missing"
    empty = self.path.parent / "empty"
    empty.mkdir()
    self.assertEqual(main(["--dir", str(missing), "summary"]), 0)
    self.assertEqual(main(["--dir", str(empty), "summary"]), 0)

def test_main_tools_missing_or_corrupt_file_exits_1(self):
    self.assertEqual(main(["--dir", str(self.path.parent), "tools", "nope.json"]), 1)
    self.path.write_text("not json")
    self.assertEqual(main(["--dir", str(self.path.parent), "tools", "0001.json"]), 1)

def test_bad_capture_does_not_stop_others_or_leak_contents(self):
    (self.path.parent / "0002.json").write_text("SECRET not json")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        self.assertEqual(main(["--dir", str(self.path.parent), "summary"]), 0)
    self.assertIn("0001.json", out.getvalue())
    self.assertIn("0002.json", out.getvalue())
    self.assertIn("estimate", out.getvalue())
    self.assertNotIn("SECRET", out.getvalue())
```

Add `import contextlib, io` and import `main`.

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `python3 -m unittest`
Expected: FAIL with `No module named 'summarize'`

- [ ] **Step 3: Implement `capture.py`**

- Estimate tokens as `len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) // 4`, and count a missing section as 0.
- A missing `model` is `"?"`.
- `--dir` defaults to `captures/` next to the script.
- `tools FILE` is resolved inside `--dir`.
- Raise `ValueError` for invalid JSON, a non-object top level, or `tools`/`messages` that aren't lists. Don't include file contents in the message.
- `OSError` on a missing `tools` file is reported the same way as `ValueError`: to stderr, with exit 1.
- The table header says tokens are estimated as characters / 4.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `python3 -m unittest`
Expected: `OK`

### Task 2: addon and proxy script

**Files:**
- Modify: `capture.py`
- Create: `.gitignore`
- Modify: `test_capture.py` (draft exists)

**Interfaces:**
- Consumes: mitmproxy's `request(flow)` and `responseheaders(flow)` module-level hooks.
- Produces: `captures/NNNN.json` files, and module constant `CAPTURES: Path`.

- [ ] **Step 1: Add these tests to `test_capture.py`**

```python
def test_undecodable_body_is_skipped_without_raising(self):
    class BadFlowRequest(SimpleNamespace):
        @property
        def content(self):
            raise ValueError("cannot decode")
    bad = SimpleNamespace(request=BadFlowRequest(path="/v1/messages", method="POST"))
    capture.request(bad)
    self.assertEqual(self.saved(), [])

def test_directory_is_private(self):
    capture.request(flow())
    self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)

def test_responses_stream_through(self):
    response = SimpleNamespace(stream=False)
    capture.responseheaders(SimpleNamespace(response=response))
    self.assertTrue(response.stream)
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `python3 -m unittest`
Expected: FAIL because `request` isn't defined yet

- [ ] **Step 3: Implement `capture.py`**

- `CAPTURES = Path(__file__).parent / "captures"`. Import nothing from mitmproxy.
- `request(flow)` matches `POST` and `flow.request.path.split("?")[0] == "/v1/messages"`.
- Reading `.content` and parsing JSON both happen inside `try/except ValueError`. Log the failure with `logging.warning`, without the body, and return.
- Create the directory with mode `0o700`. Number files from the highest existing `NNNN.json` plus 1, and create them with `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)`.
- Write `json.dumps(body, indent=2)`, keeping default ASCII escaping.
- `responseheaders(flow)` sets `flow.response.stream = True`.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `python3 -m unittest`
Expected: `OK`

- [ ] **Step 5: Add the `proxy` command and `.gitignore`**

`capture.py proxy` replaces itself with the `mitmdump` command from the spec, using this file's absolute path for `-s`. If `mitmdump` is missing it prints a clear error and returns 1. `.gitignore` contains `captures/` and `__pycache__/`.

Run: `git check-ignore captures/0001.json`
Expected: prints `captures/0001.json`

### Task 3: live verification and README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Check port 8080 is free**

Run: `lsof -nP -iTCP:8080 -sTCP:LISTEN`
Expected: no output. If something is listening, stop and report it.

- [ ] **Step 2: Start `python3 capture.py proxy` in the background**

Expected: its log shows a reverse proxy listening on `127.0.0.1:8080`.

- [ ] **Step 3: Run the isolated test**

Run: `ANTHROPIC_BASE_URL=http://localhost:8080 claude --safe-mode --no-session-persistence -p "Reply with OK"`
Expected: a reply containing `OK`. On any auth or proxy error, stop the proxy and show the error.

- [ ] **Step 4: Check the capture**

Run: `python3 capture.py summary`, then `python3 capture.py tools <file>` using a file with a non-zero tool count.
Expected: a row for each capture, and a tool breakdown.

- [ ] **Step 5: Check for secrets, printing file names only**

Run: `grep -rlE 'sk-ant-' captures/; stat -f '%Lp %N' captures captures/*`
Expected: no `grep` matches, `700` on the directory and `600` on files. Report any match without showing its contents.

- [ ] **Step 6: Stop the proxy**

Run: `lsof -nP -iTCP:8080 -sTCP:LISTEN`
Expected: no output.

- [ ] **Step 7: Write `README.md`**

Short, with the exact commands that worked:
- what it does, and that captures are sensitive
- install with `uv tool install mitmproxy`
- start the proxy, run Claude through it, summarize, show the tool breakdown, run the tests

- [ ] **Step 8: Report to Yichi and stop for Phase 2 sign-off**

Include what was built, the `capture.py` output, and anything unverified. Note any change in `~/.claude.json` or other Claude state as far as it can be observed.

---

## Phase 2: Docker with the Agent SDK

Prerequisites: `ANTHROPIC_API_KEY` in a git-ignored `.env`, and Docker running. If either is missing before Task 5's end-to-end check, stop and ask. The key must never be printed, or written to the image or a repo file. Facts from the SDK docs are in the spec.

### Task 4: key injection in `capture.py`

**Files:**
- Modify: `capture.py`, `test_capture.py`, `.gitignore`

**Interfaces:**
- Produces: `request(flow)` sets `flow.request.headers["x-api-key"]` when `UPSTREAM_API_KEY` is set, for every request.

- [ ] **Step 1: Write the failing tests**

```python
def test_injects_upstream_key_on_every_request(self):
    f = flow(path="/v1/models", method="GET")
    with mock.patch.dict(capture.os.environ, {"UPSTREAM_API_KEY": "real"}):
        capture.request(f)
    self.assertEqual(f.request.headers["x-api-key"], "real")

def test_leaves_headers_alone_without_upstream_key(self):
    f = flow()
    with mock.patch.dict(capture.os.environ, clear=True):
        capture.request(f)
    self.assertEqual(f.request.headers["x-api-key"], "SECRET")

def test_injected_key_is_not_saved(self):
    with mock.patch.dict(capture.os.environ, {"UPSTREAM_API_KEY": "real"}):
        capture.request(flow())
    self.assertNotIn("real", (self.dir / "0001.json").read_text())
```

- [ ] **Step 2: Run `python3 -m unittest`**

Expected: the first test FAILS on the header value. The other two already pass, because they guard existing behavior.

- [ ] **Step 3: Implement the injection at the top of `request(flow)`, before the path check**

- [ ] **Step 4: Run `python3 -m unittest`**

Expected: `OK`

- [ ] **Step 5: Add `transcripts/` to `.gitignore`**

### Task 5: containers

**Files:**
- Create: `agent/Dockerfile`, `agent/agent.py`, `agent/fixture.txt`, `docker-compose.yml`

**Interfaces:**
- Consumes: `capture.py`, mounted read-only.
- Produces: `docker compose run --rm agent "<prompt>"`.

- [ ] **Step 1: Check the SDK installs on this architecture**

Run: `docker run --rm python:3.12-slim sh -c "pip install -q claude-agent-sdk && pip show claude-agent-sdk | grep Version"`
Expected: a version number. Pin it in the Dockerfile. If only a source install is available, stop and report it.

- [ ] **Step 2: Write `agent/agent.py`**

Uses `ClaudeSDKClient`. `agent.py "<prompt>"` runs one turn, no prompt opens an interactive loop, and `--new` starts a fresh session. Otherwise it continues the latest session in the transcript folder. Options are `setting_sources=[]`, `system_prompt={"type": "preset", "preset": "claude_code"}`, `tools=["Read"]`, `allowed_tools=["Read"]`, `permission_mode="dontAsk"`, and `cwd="/workspace"`. Prints text blocks and the result status, and exits non-zero on an error result.

- [ ] **Step 3: Write `agent/Dockerfile`**

`python:3.12-slim`, the pinned SDK, user `1000:1000`, `fixture.txt` copied to `/workspace`, with `pineapple` as its first word.

- [ ] **Step 4: Write `docker-compose.yml`**

- `proxy`: the `mitmproxy/mitmproxy:12.3.1` image, running `mitmdump --mode reverse:https://api.anthropic.com --listen-host 0.0.0.0 --listen-port 8080 -s /app/capture.py`. Mount `./capture.py` read-only at `/app/capture.py` and `./captures` at `/app/captures`. Set `UPSTREAM_API_KEY: ${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}`. It joins `internal` and `egress`, and publishes no ports.
- `agent`: built from `agent/`, on `internal` only, depending on `proxy`. Environment: `ANTHROPIC_BASE_URL=http://proxy:8080`, `ANTHROPIC_API_KEY=placeholder`, `CLAUDE_CONFIG_DIR=/data/claude`. Set `read_only: true`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `mem_limit: 1g`, `cpus: 1`. Use tmpfs for `/tmp`, `/data/claude` and `/home`, and mount `./transcripts` at `/data/claude/projects`.
- `internal` is declared `internal: true`.

- [ ] **Step 5: Create `transcripts/` with mode 700, then run the end-to-end check**

Run: `docker compose run --rm agent "Read fixture.txt and reply with its first word"`
Expected: output contains `pineapple`. On any auth or proxy error, stop and show it. If the placeholder key is rejected by the SDK before the proxy is reached, show that too.

- [ ] **Step 6: Check the spec's conditions without printing contents**

- `python3 capture.py summary` shows new rows, and `find transcripts -name '*.jsonl'` lists a file.
- `grep -l '"tool_result"' captures/*` lists a new capture.
- A short `python3` check prints `True` for a preset marker in a new capture and `False` for `MANY SMALL FILES`.
- `tools` lists only `Read`. If more appear, report the list and rule on whether the fixture run still meets the spec.
- `docker compose run --rm agent python -c "import urllib.request as u; u.urlopen('https://api.anthropic.com', timeout=5)"` fails with a network error.
- `docker compose run --rm agent python -c "import os; print(os.environ['ANTHROPIC_API_KEY'] == 'placeholder')"` prints `True`.
- `grep -rlF "$ANTHROPIC_API_KEY" captures transcripts` prints nothing.

- [ ] **Step 7: Stop containers and update `README.md`**

Run: `docker compose down`
Expected: no containers left. Add the Docker commands and the `transcripts/` location to the README, then report to Yichi.
