import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from support import SCRIPTS


class PlatformGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsh platform "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.task = self.tmp / "task.txt"
        self.task.write_text("bounded task\n", encoding="utf-8")

    def test_supported_platform_guard(self):
        import paths

        if os.name == "posix":
            self.assertTrue(paths.require_supported()["platform_supported"])
        else:
            with self.assertRaises(paths.UnsupportedPlatform):
                paths.require_supported()

    @unittest.skipUnless(os.name != "posix", "runs on the unsupported-platform CI cell")
    def test_windows_check_fails_with_a_structured_preflight(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "run_dsh.py"),
             "--cwd", str(self.workspace), "--task-file", str(self.task),
             "--result-file", str(self.tmp / "result.json"), "--check"],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["preflight"], "unsupported_platform")
        self.assertFalse(report["platform_supported"])
        self.assertIn("Windows", report["reason"])
        self.assertFalse((self.tmp / "result.json").exists())
