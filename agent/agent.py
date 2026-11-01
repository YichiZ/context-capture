"""Continuous conversation with Claude Code's harness, inside the container.

agent.py "prompt"   run one turn
agent.py            interactive loop (exit or Ctrl-D to stop)
agent.py --new ...  start a fresh session instead of continuing the latest one
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
)

DEFAULT_WORKSPACE = "/workspace"
DEFAULT_CONFIG_DIR = "/data/claude"


def has_session():
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", DEFAULT_CONFIG_DIR)
    return any((Path(config_dir) / "projects").rglob("*.jsonl"))


def build_options(new_session):
    return ClaudeAgentOptions(
        setting_sources=[],
        system_prompt={"type": "preset", "preset": "claude_code"},
        tools=["Read"],
        allowed_tools=["Read"],
        permission_mode="dontAsk",
        cwd=os.environ.get("AGENT_WORKSPACE", DEFAULT_WORKSPACE),
        continue_conversation=not new_session and has_session(),
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


async def run(args):
    async with ClaudeSDKClient(build_options(args.new)) as client:
        if args.prompt:
            return await run_turn(client, args.prompt)
        return await interactive(client)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--new", action="store_true", help="start a fresh session")
    parser.add_argument("prompt", nargs="?", help="one turn; omit for interactive mode")
    args = parser.parse_args()
    try:
        return 0 if asyncio.run(run(args)) else 1
    except ClaudeSDKError as error:
        # The conversation is saved, so the next run continues from here.
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
