"""Continuous conversation with Claude Code's harness, inside the container.

agent.py "prompt"   run one turn
agent.py            interactive loop (exit or Ctrl-D to stop)
agent.py --new ...  start a fresh session instead of continuing the latest one
agent.py checkpoint NAME       name the current point in the conversation
agent.py checkpoints           list named points
agent.py resume NAME [PROMPT]  branch from a named point
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
    get_session_messages,
    list_sessions,
)

DEFAULT_WORKSPACE = "/workspace"
DEFAULT_CONFIG_DIR = "/data/claude"
CHECKPOINTS_FILE = "checkpoints.json"
NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class CheckpointError(Exception):
    pass


def projects_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", DEFAULT_CONFIG_DIR)) / "projects"


def workspace():
    return os.environ.get("AGENT_WORKSPACE", DEFAULT_WORKSPACE)


def has_session():
    return any(projects_dir().rglob("*.jsonl"))


def load_checkpoints():
    path = projects_dir() / CHECKPOINTS_FILE
    return json.loads(path.read_text()) if path.exists() else {}


def save_checkpoint(name):
    if not NAME_PATTERN.fullmatch(name):
        raise CheckpointError("checkpoint names use letters, numbers, - and _")
    checkpoints = load_checkpoints()
    if name in checkpoints:
        raise CheckpointError(f"checkpoint {name} already exists")
    sessions = list_sessions(directory=workspace(), limit=1)
    if not sessions:
        raise CheckpointError("no session to checkpoint yet")
    session_id = sessions[0].session_id
    messages = get_session_messages(session_id, directory=workspace())
    if not messages:
        raise CheckpointError("the latest session has no messages yet")
    point = {"session_id": session_id, "uuid": messages[-1].uuid}
    projects_dir().mkdir(parents=True, exist_ok=True)
    path = projects_dir() / CHECKPOINTS_FILE
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({**checkpoints, name: point}, indent=2))
    temporary.replace(path)


def find_checkpoint(name):
    checkpoints = load_checkpoints()
    if name not in checkpoints:
        raise CheckpointError(f"no checkpoint named {name}")
    return checkpoints[name]


def build_options(new_session, checkpoint=None):
    branch = (
        {
            "resume": checkpoint["session_id"],
            "resume_session_at": checkpoint["uuid"],
            "fork_session": True,
        }
        if checkpoint
        else {}
    )
    return ClaudeAgentOptions(
        setting_sources=[],
        system_prompt={"type": "preset", "preset": "claude_code"},
        tools=["Read"],
        allowed_tools=["Read"],
        permission_mode="dontAsk",
        cwd=workspace(),
        continue_conversation=not new_session and not checkpoint and has_session(),
        **branch,
    )


async def run_turn(client, prompt):
    await client.query(prompt)
    succeeded = False
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            succeeded = not message.is_error
            print(f"[{message.subtype}]", file=sys.stderr)
    return succeeded


async def interactive(client):
    all_succeeded = True
    while True:
        try:
            prompt = input("> ").strip()
        except EOFError:
            return all_succeeded
        if prompt == "exit":
            return all_succeeded
        if prompt:
            all_succeeded = await run_turn(client, prompt) and all_succeeded


async def converse(options, prompt):
    async with ClaudeSDKClient(options) as client:
        if prompt:
            return await run_turn(client, prompt)
        return await interactive(client)


def run_command(argv):
    """Run a checkpoint command, or return None if argv is a normal run."""
    if len(argv) == 2 and argv[0] == "checkpoint":
        save_checkpoint(argv[1])
        print(f"saved {argv[1]}")
        return 0
    if argv == ["checkpoints"]:
        print("\n".join(load_checkpoints()) or "no checkpoints")
        return 0
    if len(argv) in (2, 3) and argv[0] == "resume":
        options = build_options(False, find_checkpoint(argv[1]))
        prompt = argv[2] if len(argv) == 3 else None
        return 0 if asyncio.run(converse(options, prompt)) else 1
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--new", action="store_true", help="start a fresh session")
    parser.add_argument("prompt", nargs="?", help="one turn; omit for interactive mode")
    try:
        result = run_command(sys.argv[1:])
        if result is not None:
            return result
        args = parser.parse_args()
        return 0 if asyncio.run(converse(build_options(args.new), args.prompt)) else 1
    except (ClaudeSDKError, CheckpointError) as error:
        # The conversation is saved, so the next run continues from here.
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
