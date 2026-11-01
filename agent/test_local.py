"""Tests agent/local.sh with stub lsof, ps and uv, so nothing reaches the API."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "local.sh"
FIXTURE = Path(__file__).resolve().parent / "fixture.txt"
PROXY_ARGS = "python mitmdump --mode reverse:https://api.anthropic.com -s /repo/capture.py"


class LocalScriptTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / "repo"
        (self.repo / "agent").mkdir(parents=True)
        shutil.copy(SCRIPT, self.repo / "agent" / "local.sh")
        shutil.copy(FIXTURE, self.repo / "agent" / "fixture.txt")
        (self.repo / "agent" / "agent.py").write_text("")
        self.env_file = self.repo / ".env"
        self.env_file.write_text('ANTHROPIC_API_KEY="real-key"\nOTHER_SECRET=nope\n')
        self.stubs = self.root / "stubs"
        self.stubs.mkdir()
        self.record = self.root / "uv.json"
        self.stub("lsof", 'echo 4242')
        self.stub("ps", f'echo "{PROXY_ARGS}"')
        self.stub("uv", (
            f'exec python3 -c "import json, os, sys; '
            f"json.dump({{'env': dict(os.environ), 'cwd': os.getcwd(), 'args': sys.argv[1:]}}, "
            f"open('{self.record}', 'w'))\" \"$@\""
        ))

    def stub(self, name, body):
        path = self.stubs / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def run_script(self, **extra):
        env = {
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "HOME": str(self.root / "home"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            **extra,
        }
        return subprocess.run(
            ["sh", str(self.repo / "agent" / "local.sh"), "hello"],
            env=env, capture_output=True, text=True,
        )

    def seen(self):
        return json.loads(self.record.read_text())["env"]

    def test_refuses_when_nothing_listens(self):
        self.stub("lsof", "exit 1")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("capture.py proxy", result.stderr)
        self.assertFalse(self.record.exists())

    def test_refuses_another_server_on_the_port(self):
        self.stub("ps", 'echo "node dev-server.js"')
        self.assertEqual(self.run_script().returncode, 1)
        self.assertFalse(self.record.exists())

    def test_passes_only_the_unquoted_key_from_env_file(self):
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.seen()["ANTHROPIC_API_KEY"], "real-key")
        self.assertNotIn("OTHER_SECRET", self.seen())

    def test_does_not_execute_env_file(self):
        marker = self.root / "executed"
        self.env_file.write_text(f'ANTHROPIC_API_KEY=real-key\n$(touch {marker})\nNOTE=two words\n')
        self.assertEqual(self.run_script().returncode, 0)
        self.assertFalse(marker.exists())

    def test_clears_ambient_claude_and_anthropic_settings(self):
        result = self.run_script(
            CLAUDE_CODE_USE_BEDROCK="1", ANTHROPIC_AUTH_TOKEN="x", ANTHROPIC_MODEL="y",
        )
        self.assertEqual(result.returncode, 0)
        seen = self.seen()
        for name in ("CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"):
            self.assertNotIn(name, seen)
        self.assertEqual(seen["ANTHROPIC_BASE_URL"], "http://localhost:8080")

    def test_workspace_is_outside_repo_and_holds_only_the_fixture(self):
        self.assertEqual(self.run_script().returncode, 0)
        workspace = Path(self.seen()["AGENT_WORKSPACE"])
        self.assertNotIn(self.repo, workspace.parents)
        self.assertEqual([p.name for p in workspace.iterdir()], ["fixture.txt"])


if __name__ == "__main__":
    unittest.main()
