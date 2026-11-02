import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from support import POSIX_ONLY, SCRIPTS, SKILL, install_fake, read_json, wait_process_gone

RUN = SCRIPTS / "run_dsh.py"


@POSIX_ONLY
class RunDSHTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsh run "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workspace = self.tmp / "workspace with spaces [x]"
        self.workspace.mkdir()
        self.fake = install_fake(self.tmp / "fake dsh")
        self.task = self.tmp / "task one.txt"
        self.task.write_text("Implement the task and report checks.\n", encoding="utf-8")
        self.result = self.tmp / "result one.json"
        self.events = self.tmp / "events one.jsonl"
        self.pidfile = self.tmp / "fake pids.json"
        self.log = self.tmp / "calls.jsonl"
        self.env = {
            **os.environ,
            "FAKE_DSH_LOG": str(self.log),
            "FAKE_DSH_MODE": "complete",
            "FAKE_DSH_PIDFILE": str(self.pidfile),
            "DEEPSEEK_API_KEY": "sk-test-should-not-leak",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def command(self, *extra):
        return [
            sys.executable, str(RUN),
            "--cwd", str(self.workspace),
            "--task-file", str(self.task),
            "--result-file", str(self.result),
            "--dsh", str(self.fake),
            "--timeout", "20",
            *extra,
        ]

    def test_report_and_console_filter_thinking_tool_bodies_and_credentials(self):
        completed = subprocess.run(
            self.command("--events-file", str(self.events)),
            capture_output=True, text=True, env=self.env, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("SECRET_THOUGHT", completed.stdout)
        self.assertNotIn("SECRET_RAW_ARG", completed.stdout)
        self.assertNotIn("SECRET_RAW_RESULT", completed.stdout)
        self.assertNotIn("sk-test-should-not-leak", completed.stdout)

        report = read_json(self.result)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["final"], "Completed fake task.")
        self.assertEqual(report["task_sha256"], __import__("hashlib").sha256(self.task.read_bytes()).hexdigest())
        self.assertNotIn("SECRET_THOUGHT", json.dumps(report))
        self.assertNotIn("SECRET_RAW_ARG", json.dumps(report))
        self.assertNotIn("SECRET_RAW_RESULT", json.dumps(report))
        self.assertNotIn("sk-test-should-not-leak", json.dumps(report))
        self.assertIn({"type": "tool_call", "tool": "shell"}, report["events"])
        self.assertIn({"type": "tool_result", "status": "ok"}, report["events"])
        self.assertEqual(stat.S_IMODE(self.result.stat().st_mode), 0o600)
        events_text = self.events.read_text(encoding="utf-8")
        self.assertNotIn("SECRET_THOUGHT", events_text)
        self.assertNotIn("SECRET_RAW_RESULT", events_text)

    def test_result_is_never_overwritten(self):
        subprocess.run(self.command(), capture_output=True, text=True, env=self.env, timeout=30)
        second = subprocess.run(self.command(), capture_output=True, text=True, env=self.env, timeout=30)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already exists", second.stderr + second.stdout)

    def test_check_is_offline_and_writes_no_report(self):
        completed = subprocess.run(
            self.command("--check"), capture_output=True, text=True, env=self.env, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["preflight"], "passed")
        self.assertFalse(report["model_called"])
        self.assertFalse(self.result.exists())
        self.assertEqual(report["skill_root"], str(SKILL))

    def test_install_relative_copy_with_special_characters(self):
        copied = self.tmp / "copied skill [ü]"
        shutil.copytree(SKILL, copied)
        copied_result = self.tmp / "copied result [1].json"
        copied_task = self.tmp / "copied task [1].txt"
        copied_task.write_text("copy test\n", encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable, str(copied / "scripts" / "run_dsh.py"),
                "--cwd", str(self.workspace), "--task-file", str(copied_task),
                "--result-file", str(copied_result), "--dsh", str(self.fake),
                "--timeout", "20", "--check",
            ],
            capture_output=True, text=True, env=self.env, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["skill_root"], str(copied.resolve()))
        self.assertFalse(copied_result.exists())

    def test_timeout_kills_the_private_process_group(self):
        self.env["FAKE_DSH_MODE"] = "hang"
        completed = subprocess.run(
            self.command("--timeout", "8"),
            capture_output=True, text=True, env=self.env, timeout=30,
        )
        self.assertNotEqual(completed.returncode, 0)
        report = read_json(self.result)
        self.assertEqual(report["status"], "timeout")
        pids = read_json(self.pidfile)
        self.assertTrue(wait_process_gone(pids["dsh"]))
        self.assertTrue(wait_process_gone(pids["child"]))
