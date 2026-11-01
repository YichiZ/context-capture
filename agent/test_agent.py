"""Runs on the host with a stub SDK: python3 -m unittest discover -s agent"""
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


class FakeSDKError(Exception):
    pass


class FakeResult:
    def __init__(self, is_error):
        self.is_error, self.subtype, self.session_id = is_error, "success", "fork"


stub = types.ModuleType("claude_agent_sdk")
stub.ClaudeSDKError = FakeSDKError
stub.AssistantMessage = type("AssistantMessage", (), {})
stub.TextBlock = type("TextBlock", (), {})
stub.ResultMessage = FakeResult
stub.ClaudeAgentOptions = lambda **kwargs: kwargs
stub.ClaudeSDKClient = None
stub.list_sessions = lambda **_: []
stub.get_session_messages = lambda *_, **__: []
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


class OptionsTest(unittest.TestCase):
    def test_docker_defaults(self):
        with mock.patch.dict(os.environ, clear=True):
            self.assertEqual(agent.build_options(True)["cwd"], "/workspace")

    def test_workspace_override(self):
        with mock.patch.dict(os.environ, {"AGENT_WORKSPACE": "/tmp/work"}):
            self.assertEqual(agent.build_options(True)["cwd"], "/tmp/work")

    def test_continues_only_with_a_session_in_config_dir(self):
        with tempfile.TemporaryDirectory() as config_dir:
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": config_dir}):
                self.assertFalse(agent.build_options(False)["continue_conversation"])
                session = Path(config_dir) / "projects" / "workspace" / "one.jsonl"
                session.parent.mkdir(parents=True)
                session.write_text("{}")
                self.assertTrue(agent.build_options(False)["continue_conversation"])
                self.assertFalse(agent.build_options(True)["continue_conversation"])


def finished(uuid):
    return types.SimpleNamespace(
        uuid=uuid, type="assistant", message={"content": [{"type": "text", "text": "ok"}]}
    )


class AgentTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp.name})
        patch.start()
        self.addCleanup(patch.stop)

    def test_interactive_exits_0_when_turns_succeed(self):
        self.assertEqual(run_main(FakeClient([False]), [], ["hi", "exit"]), 0)

    def test_interactive_keeps_going_after_failed_turn_and_exits_1(self):
        client = FakeClient([True, False])
        self.assertEqual(run_main(client, [], ["one", "two", EOFError()]), 1)
        self.assertEqual(client.results, [])

    def test_sdk_error_exits_1_without_traceback(self):
        self.assertEqual(run_main(FakeClient([FakeSDKError("boom")]), [], ["hi"]), 1)
        self.assertEqual(run_main(FakeClient([FakeSDKError("boom")]), ["hi"]), 1)


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = Path(tmp.name)
        patches = [
            mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp.name}),
            mock.patch.object(agent, "list_sessions", lambda **_: [types.SimpleNamespace(session_id="s1")]),
            mock.patch.object(
                agent, "get_session_messages",
                lambda *_, **__: [finished("u1"), finished("u2")],
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_cli(self, *argv):
        with mock.patch.object(sys, "argv", ["agent.py", *argv]):
            return agent.main()

    def test_checkpoint_saves_latest_session_and_last_message(self):
        self.assertEqual(self.run_cli("checkpoint", "before"), 0)
        saved = json.loads((self.config / "projects" / "checkpoints.json").read_text())
        self.assertEqual(saved, {"before": {"session_id": "s1", "uuid": "u2"}})

    def test_checkpoint_names_are_immutable(self):
        self.run_cli("checkpoint", "before")
        self.assertEqual(self.run_cli("checkpoint", "before"), 1)

    def test_checkpoint_rejects_path_like_name(self):
        self.assertEqual(self.run_cli("checkpoint", "../x"), 1)

    def test_checkpoint_needs_a_session(self):
        with mock.patch.object(agent, "list_sessions", lambda **_: []):
            self.assertEqual(self.run_cli("checkpoint", "before"), 1)

    def test_checkpoint_needs_messages(self):
        with mock.patch.object(agent, "get_session_messages", lambda *_, **__: []):
            self.assertEqual(self.run_cli("checkpoint", "before"), 1)

    def test_checkpoints_lists_names(self):
        self.run_cli("checkpoint", "before")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.run_cli("checkpoints"), 0)
        self.assertIn("before", out.getvalue())

    def test_resume_unknown_checkpoint_fails(self):
        self.assertEqual(self.run_cli("resume", "missing", "hi"), 1)

    def test_resume_branches_at_the_checkpoint(self):
        self.run_cli("checkpoint", "before")
        seen = {}

        def client(options):
            seen.update(options)
            return FakeClient([False])

        with mock.patch.object(agent, "ClaudeSDKClient", client):
            self.assertEqual(self.run_cli("resume", "before", "hi"), 0)
        self.assertEqual(seen["resume"], "s1")
        self.assertEqual(seen["resume_session_at"], "u2")
        self.assertTrue(seen["fork_session"])
        self.assertFalse(seen["continue_conversation"])

    def options_for_next_run(self, *argv, results=(False,)):
        seen = {}

        def client(options):
            seen.clear()
            seen.update(options)
            return FakeClient(list(results))

        with mock.patch.object(agent, "ClaudeSDKClient", client):
            code = self.run_cli(*argv)
        return code, seen

    def test_missing_name_is_a_usage_error_not_a_prompt(self):
        for argv in (["checkpoint"], ["resume"]):
            client = mock.Mock()
            with mock.patch.object(agent, "ClaudeSDKClient", client):
                self.assertEqual(self.run_cli(*argv), 1)
            client.assert_not_called()

    def test_plain_run_after_failed_resume_still_branches(self):
        self.run_cli("checkpoint", "before")
        code, _ = self.options_for_next_run("resume", "before", "q", results=[FakeSDKError("boom")])
        self.assertEqual(code, 1)
        _, seen = self.options_for_next_run("retry")
        self.assertEqual(seen["resume"], "s1")
        self.assertEqual(seen["resume_session_at"], "u2")
        self.assertTrue(seen["fork_session"])

    def test_plain_run_after_successful_resume_continues_the_branch(self):
        self.run_cli("checkpoint", "before")
        self.options_for_next_run("resume", "before", "q")
        _, seen = self.options_for_next_run("next")
        self.assertEqual(seen["resume"], "fork")
        self.assertNotIn("resume_session_at", seen)
        self.assertFalse(seen["continue_conversation"])

    def test_checkpoint_after_resume_without_a_turn_keeps_the_branch_point(self):
        self.run_cli("checkpoint", "before")
        self.options_for_next_run("resume", "before", "q", results=[FakeSDKError("boom")])
        with mock.patch.object(agent, "get_session_messages", lambda *_, **__: [finished("tip")]):
            self.run_cli("checkpoint", "here")
        saved = json.loads((self.config / "projects" / "checkpoints.json").read_text())
        self.assertEqual(saved["here"], saved["before"])

    def test_checkpoint_follows_the_active_session(self):
        self.run_cli("checkpoint", "before")
        self.options_for_next_run("resume", "before", "q")
        calls = []

        def messages(session_id, **_):
            calls.append(session_id)
            return [finished("u9")]

        with mock.patch.object(agent, "get_session_messages", messages):
            self.run_cli("checkpoint", "later")
        self.assertEqual(calls, ["fork"])

    def test_new_session_ignores_the_active_session(self):
        self.run_cli("checkpoint", "before")
        self.options_for_next_run("resume", "before", "q")
        _, seen = self.options_for_next_run("--new", "fresh")
        self.assertNotIn("resume", seen)

    def test_checkpoint_refuses_an_unfinished_turn(self):
        unanswered = types.SimpleNamespace(uuid="u3", type="user", message={"content": "q"})
        tool_call = types.SimpleNamespace(
            uuid="u4", type="assistant", message={"content": [{"type": "tool_use"}]}
        )
        for last in (unanswered, tool_call):
            with mock.patch.object(agent, "get_session_messages", lambda *_, l=last, **__: [l]):
                self.assertEqual(self.run_cli("checkpoint", "bad"), 1)

    def test_a_prompt_mentioning_a_command_is_still_a_prompt(self):
        prompts = []

        class Recording(FakeClient):
            async def query(self, prompt):
                prompts.append(prompt)

        with mock.patch.object(agent, "ClaudeSDKClient", lambda _: Recording([False])):
            self.assertEqual(self.run_cli("resume the work"), 0)
        self.assertEqual(prompts, ["resume the work"])


if __name__ == "__main__":
    unittest.main()
