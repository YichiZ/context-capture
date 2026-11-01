# Resume sessions plan

**Goal:** name points in a conversation and branch from them, in Docker and local mode.

## Changes

`agent/agent.py` gains three commands:

- `checkpoint NAME`: saves the latest session's ID and last message UUID.
- `checkpoints`: lists saved names.
- `resume NAME [PROMPT]`: branches from a checkpoint with `resume`, `resume_session_at` and
  `fork_session`, leaving the original session intact.

Checkpoints live in `CLAUDE_CONFIG_DIR/projects/checkpoints.json`, so they persist in
`./transcripts` for Docker and `.claude-local/projects` for local mode. Names are immutable.
The current position is saved in `active.json`, so plain runs and checkpoints never guess from
file timestamps. A resume that hasn't produced a reply yet stays pending and is retried.

File changes aren't undone, because the workspace is read-only in Docker.

## Tests

Unit tests with a stub SDK: saving, duplicate and invalid names, no session, listing, unknown
checkpoint, resume options, and command parsing.

## Verify

Run in fresh processes, with a new random code word for each mode:

1. Tell Claude nothing secret, then `checkpoint before`.
2. Give it the code word, then `checkpoint after`.
3. `resume before`: the code word is absent from every new capture and the reply.
4. A plain run on that branch: still absent.
5. `resume after`: the code word is back, in both the reply and the capture.
6. The original session's transcript is unchanged by the resumes.
7. The code word is absent from the branch's own transcript from step 3.

If resume fails these checks, stop and show Yichi before trying the proxy fallback.
