"""Runs on the host with a stub SDK: python3 -m unittest discover -s agent"""
import sys
import types
import unittest
from unittest import mock


class FakeSDKError(Exception):
    pass


class FakeResult:
    def __init__(self, is_error):
        self.is_error, self.subtype = is_error, "success"


stub = types.ModuleType("claude_agent_sdk")
stub.ClaudeSDKError = FakeSDKError
stub.AssistantMessage = type("AssistantMessage", (), {})
stub.TextBlock = type("TextBlock", (), {})
stub.ResultMessage = FakeResult
stub.ClaudeAgentOptions = lambda **kwargs: kwargs
stub.ClaudeSDKClient = None
sys.modules["claude_agent_sdk"] = stub

import agent  # noqa: E402


class FakeClient:
    def __init__(self, results):
        self.results = list(results)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def query(self, prompt):
        if isinstance(self.results[0], Exception):
            raise self.results.pop(0)

    async def receive_response(self):
        yield FakeResult(self.results.pop(0))


def run_main(client, argv, inputs=()):
    with mock.patch.object(agent, "ClaudeSDKClient", lambda _: client), \
            mock.patch.object(sys, "argv", ["agent.py", *argv]), \
            mock.patch("builtins.input", side_effect=list(inputs)):
        return agent.main()


class AgentTest(unittest.TestCase):
    def test_interactive_exits_0_when_turns_succeed(self):
        self.assertEqual(run_main(FakeClient([False]), [], ["hi", "exit"]), 0)

    def test_interactive_keeps_going_after_failed_turn_and_exits_1(self):
        client = FakeClient([True, False])
        self.assertEqual(run_main(client, [], ["one", "two", EOFError()]), 1)
        self.assertEqual(client.results, [])

    def test_sdk_error_exits_1_without_traceback(self):
        self.assertEqual(run_main(FakeClient([FakeSDKError("boom")]), [], ["hi"]), 1)
        self.assertEqual(run_main(FakeClient([FakeSDKError("boom")]), ["hi"]), 1)


if __name__ == "__main__":
    unittest.main()
