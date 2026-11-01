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
ACTIVE_FILE = "active.json"
NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class CheckpointError(Exception):
    pass


def projects_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", DEFAULT_CONFIG_DIR)) / "projects"


def workspace():
    return os.environ.get("AGENT_WORKSPACE", DEFAULT_WORKSPACE)


def has_session():
    return any(projects_dir().rglob("*.jsonl"))


def load_state(file_name):
    path = projects_dir() / file_name
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(file_name, value):
    projects_dir().mkdir(parents=True, exist_ok=True)
    path = projects_dir() / file_name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def set_active(session_id, uuid=None):
    """Record where the conversation is. A uuid means a branch that isn't created yet."""
    save_state(ACTIVE_FILE, {"session_id": session_id, "uuid": uuid})


def is_finished_turn(message):
    if message.type != "assistant":
        return False
    content = message.message.get("content", [])
    return not any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)


def current_point():
    active = load_state(ACTIVE_FILE)
    if active.get("uuid"):
        return {"session_id": active["session_id"], "uuid": active["uuid"]}
    session_id = active.get("session_id")
    if not session_id:
        sessions = list_sessions(directory=workspace(), limit=1)
        if not sessions:
            raise CheckpointError("no session to checkpoint yet")
        session_id = sessions[0].session_id
    messages = get_session_messages(session_id, directory=workspace())
    if not messages:
        raise CheckpointError("the latest session has no messages yet")
    if not is_finished_turn(messages[-1]):
        raise CheckpointError("the last turn didn't finish, so there's nothing safe to save")
    return {"session_id": session_id, "uuid": messages[-1].uuid}


def save_checkpoint(name):
    if not NAME_PATTERN.fullmatch(name):
        raise CheckpointError("checkpoint names use letters, numbers, - and _")
    checkpoints = load_state(CHECKPOINTS_FILE)
    if name in checkpoints:
        raise CheckpointError(f"checkpoint {name} already exists")
    save_state(CHECKPOINTS_FILE, {**checkpoints, name: current_point()})


def find_checkpoint(name):
    checkpoints = load_state(CHECKPOINTS_FILE)
    if name not in checkpoints:
        raise CheckpointError(f"no checkpoint named {name}")
    return checkpoints[name]


def session_options(active):
    if active.get("uuid"):
        return {
            "resume": active["session_id"],
            "resume_session_at": active["uuid"],
            "fork_session": True,
        }
    return {"resume": active["session_id"]} if active.get("session_id") else {}


def build_options(new_session):
    active = {} if new_session else load_state(ACTIVE_FILE)
    branch = session_options(active)
    return ClaudeAgentOptions(
        setting_sources=[],
        system_prompt={"type": "preset", "preset": "claude_code"},
        tools=["Read"],
        allowed_tools=["Read"],
        permission_mode="dontAsk",
        cwd=workspace(),
        continue_conversation=not new_session and not branch and has_session(),
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
            if succeeded:
                set_active(message.session_id)
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
    if argv in (["checkpoint"], ["resume"]):
        raise CheckpointError(f"{argv[0]} needs a checkpoint name")
    if len(argv) == 2 and argv[0] == "checkpoint":
        save_checkpoint(argv[1])
        print(f"saved {argv[1]}")
        return 0
    if argv == ["checkpoints"]:
        print("\n".join(load_state(CHECKPOINTS_FILE)) or "no checkpoints")
        return 0
    if len(argv) in (2, 3) and argv[0] == "resume":
        checkpoint = find_checkpoint(argv[1])
        set_active(checkpoint["session_id"], checkpoint["uuid"])
        prompt = argv[2] if len(argv) == 3 else None
        return 0 if asyncio.run(converse(build_options(False), prompt)) else 1
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
