import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import capture
from capture import main, summarize_capture, tool_sizes

FAKE = {
    "model": "claude-test",
    "system": [{"type": "text", "text": "x" * 400}],
    "tools": [
        {"name": "small", "input_schema": {}},
        {"name": "big", "description": "y" * 400, "input_schema": {}},
    ],
    "messages": [{"role": "user", "content": "hi"}],
}



class SummarizeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "0001.json"
        self.path.write_text(json.dumps(FAKE))

    def test_row(self):
        row = summarize_capture(self.path)
        self.assertEqual(row["file"], "0001.json")
        self.assertEqual(row["model"], "claude-test")
        self.assertEqual((row["messages"], row["tools"]), (1, 2))
        self.assertGreaterEqual(row["system_tokens"], 100)
        self.assertLess(row["system_tokens"], 120)
        self.assertGreater(row["tools_tokens"], 100)
        self.assertLess(row["messages_tokens"], 20)

    def test_missing_fields_are_zero(self):
        self.path.write_text("{}")
        row = summarize_capture(self.path)
        self.assertEqual(row["model"], "?")
        self.assertEqual(row["messages"] + row["tools"], 0)
        self.assertEqual(row["system_tokens"], 0)

    def test_tool_sizes_largest_first(self):
        names = [name for name, _ in tool_sizes(self.path)]
        self.assertEqual(names, ["big", "small"])

    def test_bad_capture_raises_value_error(self):
        for body in ("not json", "[]", '{"tools": 3}'):
            self.path.write_text(body)
            with self.assertRaises(ValueError):
                summarize_capture(self.path)

    def test_system_as_string(self):
        self.path.write_text(json.dumps({"system": "x" * 400}))
        self.assertGreaterEqual(summarize_capture(self.path)["system_tokens"], 100)

    def test_unnamed_tool_is_question_mark(self):
        self.path.write_text(json.dumps({"tools": [{"type": "server"}]}))
        self.assertEqual(tool_sizes(self.path)[0][0], "?")

    def test_null_or_non_string_tool_name_is_question_mark(self):
        self.path.write_text(json.dumps({"tools": [{"name": None}, {"name": ["x"]}, {"name": ""}]}))
        self.assertEqual({name for name, _ in tool_sizes(self.path)}, {"?"})
        self.assertEqual(main(["--dir", str(self.path.parent), "tools", "0001.json"]), 0)

    def test_deeply_nested_capture_is_bad_capture(self):
        self.path.write_text("[" * 200_000)
        with self.assertRaises(ValueError):
            summarize_capture(self.path)

    def test_lone_surrogate_does_not_abort_cli(self):
        (self.path.parent / "0002.json").write_text('{"model": "\\ud800"}')
        script = Path(capture.__file__).resolve()
        result = subprocess.run(
            [sys.executable, str(script), "--dir", str(self.path.parent), "summary"],
            capture_output=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0002.json", result.stdout)

    def test_main_handles_empty_and_missing_dir(self):
        missing = self.path.parent / "missing"
        empty = self.path.parent / "empty"
        empty.mkdir()
        self.assertEqual(main(["--dir", str(missing), "summary"]), 0)
        self.assertEqual(main(["--dir", str(empty), "summary"]), 0)

    def test_main_tools_missing_or_corrupt_file_exits_1(self):
        d = str(self.path.parent)
        self.assertEqual(main(["--dir", d, "tools", "nope.json"]), 1)
        self.path.write_text("not json")
        self.assertEqual(main(["--dir", d, "tools", "0001.json"]), 1)

    def test_tools_output_lists_tools(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--dir", str(self.path.parent), "tools", "0001.json"]), 0)
        self.assertLess(out.getvalue().index("big"), out.getvalue().index("small"))

    def test_bad_capture_does_not_stop_others_or_leak_contents(self):
        (self.path.parent / "0002.json").write_text("SECRET not json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--dir", str(self.path.parent), "summary"]), 0)
        self.assertIn("0001.json", out.getvalue())
        self.assertIn("0002.json", out.getvalue())
        self.assertIn("estimate", out.getvalue())
        self.assertNotIn("SECRET", out.getvalue())


def flow(path="/v1/messages?beta=true", method="POST", body=b'{"model": "m"}'):
    request = SimpleNamespace(
        path=path, method=method, content=body, headers={"x-api-key": "SECRET"}
    )
    return SimpleNamespace(request=request)


class SaveRequestsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "captures"
        patcher = mock.patch.object(capture, "CAPTURES", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def saved(self):
        return sorted(p.name for p in self.dir.glob("*")) if self.dir.exists() else []

    def test_saves_body_only_numbered_and_private(self):
        capture.request(flow())
        capture.request(flow())
        self.assertEqual(self.saved(), ["0001.json", "0002.json"])
        text = (self.dir / "0001.json").read_text()
        self.assertEqual(json.loads(text), {"model": "m"})
        self.assertNotIn("SECRET", text)
        self.assertEqual(stat.S_IMODE((self.dir / "0001.json").stat().st_mode), 0o600)

    def test_numbering_continues_after_restart(self):
        self.dir.mkdir()
        (self.dir / "0007.json").write_text("{}")
        capture.request(flow())
        self.assertIn("0008.json", self.saved())

    def test_ignores_other_requests(self):
        capture.request(flow(path="/v1/messages/count_tokens?beta=true"))
        capture.request(flow(method="GET"))
        capture.request(flow(path="/v1/models"))
        self.assertEqual(self.saved(), [])

    def test_non_json_body_is_skipped(self):
        capture.request(flow(body=b"nope"))
        self.assertEqual(self.saved(), [])

    def test_undecodable_body_is_skipped_without_raising(self):
        class BadRequest(SimpleNamespace):
            @property
            def content(self):
                raise ValueError("cannot decode")

        capture.request(SimpleNamespace(request=BadRequest(path="/v1/messages", method="POST")))
        self.assertEqual(self.saved(), [])

    def test_directory_is_private(self):
        capture.request(flow())
        self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)

    def test_responses_stream_through(self):
        response = SimpleNamespace(stream=False)
        capture.responseheaders(SimpleNamespace(response=response))
        self.assertTrue(response.stream)

    def test_injects_upstream_key_on_messages_paths_only(self):
        for path in ("/v1/messages?beta=true", "/v1/messages/count_tokens"):
            f = flow(path=path)
            with mock.patch.dict(capture.os.environ, {"UPSTREAM_API_KEY": "real"}):
                capture.request(f)
            self.assertEqual(f.request.headers["x-api-key"], "real")

    def test_other_paths_never_get_the_upstream_key(self):
        for path in ("/v1/files", "/v1/messages/batches", "/v1/messages/../files", "/v1/models"):
            f = flow(path=path, method="GET")
            with mock.patch.dict(capture.os.environ, {"UPSTREAM_API_KEY": "real"}):
                capture.request(f)
            self.assertEqual(f.request.headers["x-api-key"], "SECRET")

    def test_injection_removes_client_authorization(self):
        f = flow()
        f.request.headers["authorization"] = "Bearer client"
        with mock.patch.dict(capture.os.environ, {"UPSTREAM_API_KEY": "real"}):
            capture.request(f)
        self.assertNotIn("authorization", f.request.headers)

    def test_local_proxy_ignores_upstream_key_in_shell(self):
        seen = {}
        def fake_exec(*_):
            seen.update(capture.os.environ)
        with mock.patch.dict(capture.os.environ, {"UPSTREAM_API_KEY": "real"}):
            with mock.patch.object(capture.os, "execvp", fake_exec):
                main(["proxy"])
        self.assertNotIn("UPSTREAM_API_KEY", seen)

    def test_leaves_headers_alone_without_upstream_key(self):
        f = flow()
        with mock.patch.dict(capture.os.environ, clear=True):
            capture.request(f)
        self.assertEqual(f.request.headers["x-api-key"], "SECRET")

    def test_injected_key_is_not_saved(self):
        with mock.patch.dict(capture.os.environ, {"UPSTREAM_API_KEY": "real"}):
            capture.request(flow())
        self.assertNotIn("real", (self.dir / "0001.json").read_text())

    def test_proxy_command_is_local_reverse_proxy_with_this_addon(self):
        with mock.patch.object(capture.os, "execvp") as execvp:
            main(["proxy"])
        args = execvp.call_args.args[1]
        self.assertIn("reverse:https://api.anthropic.com", args)
        self.assertEqual(args[args.index("--listen-host") + 1], "127.0.0.1")
        self.assertEqual(args[args.index("--listen-port") + 1], "8080")
        self.assertEqual(args[args.index("-s") + 1], str(Path(capture.__file__).resolve()))

    def test_proxy_reports_missing_mitmdump(self):
        with mock.patch.object(capture.os, "execvp", side_effect=FileNotFoundError):
            self.assertEqual(main(["proxy"]), 1)


if __name__ == "__main__":
    unittest.main()
