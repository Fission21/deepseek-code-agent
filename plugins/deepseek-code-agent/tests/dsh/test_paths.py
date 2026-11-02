import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from support import POSIX_ONLY, SCRIPTS, SKILL


class PathResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsh paths "))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_platform_report_and_windows_guard(self):
        import paths

        info = paths.platform_info()
        if os.name == "posix":
            self.assertIn(info["platform"], ("darwin", "linux"))
            self.assertTrue(info["platform_supported"])
            self.assertIsNone(info["reason"])
        else:
            self.assertFalse(info["platform_supported"])
            self.assertIn("Windows", info["reason"])
        with mock.patch.object(paths.sys, "platform", "win32"):
            report = paths.preflight()
            self.assertFalse(report["platform_supported"])
            self.assertEqual(report["preflight"], "unsupported_platform")
            self.assertIn("Windows", report["reason"])
            with self.assertRaises(paths.UnsupportedPlatform):
                paths.require_supported()

    @POSIX_ONLY
    def test_resolve_dsh_and_runtime_root_from_app_layout(self):
        import paths

        resources = self.tmp / "App" / "Contents" / "Resources"
        (resources / "runtime" / "cli" / "bin").mkdir(parents=True)
        dsh = resources / "runtime" / "cli" / "bin" / "dsh"
        dsh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        dsh.chmod(0o755)
        (resources / "app.asar").write_text("asar placeholder", encoding="utf-8")
        self.assertEqual(paths.resolve_dsh(str(dsh)), str(dsh.resolve()))
        self.assertEqual(
            paths.runtime_root_for(str(dsh)),
            str(resources.resolve() / "app.asar" / "dsh" / "node_modules" / "@deepseek-ai"),
        )
        with mock.patch.dict(os.environ, {"DSH_RUNTIME_ROOT": str(self.tmp / "runtime")}):
            self.assertEqual(paths.runtime_root_for(str(dsh)), str((self.tmp / "runtime").absolute()))

    def test_huashu_resolution_and_version_check(self):
        import paths

        package = self.tmp / "huashu-package"
        (package / "src").mkdir(parents=True)
        (package / "package.json").write_text(
            json.dumps({"name": "huashu-chrome", "version": "1.3.0"}), encoding="utf-8"
        )
        entry = package / "src" / "cli.js"
        entry.write_text("// cli\n", encoding="utf-8")
        self.assertEqual(paths.resolve_huashu(str(entry)), str(entry.resolve()))
        with mock.patch.dict(os.environ, {"HUASHU_CHROME_VERSION": "9.9.9"}):
            with self.assertRaises(FileNotFoundError):
                paths.resolve_huashu(str(entry))

    @POSIX_ONLY
    def test_node_resolution_honors_explicit_binary(self):
        import paths

        node = self.tmp / "fake node"
        node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        node.chmod(0o755)
        self.assertEqual(paths.resolve_node(str(node)), str(node.resolve()))
        previous = os.getcwd()
        os.chdir(self.tmp)
        try:
            self.assertEqual(paths.resolve_node(node.name), str(node.resolve()))
        finally:
            os.chdir(previous)
        with self.assertRaises(FileNotFoundError):
            paths.resolve_node(str(self.tmp / "missing-node"))
        with mock.patch.dict(os.environ, {"NODE_BIN": str(self.tmp / "missing-node")}):
            with self.assertRaises(FileNotFoundError):
                paths.resolve_node()

    def test_explicit_dsh_and_huashu_errors_do_not_fall_back(self):
        import paths

        with self.assertRaises(FileNotFoundError) as dsh_error:
            paths.resolve_dsh(str(self.tmp / "missing-dsh"))
        self.assertIn("no other installation", str(dsh_error.exception))

        with self.assertRaises(FileNotFoundError) as huashu_error:
            paths.resolve_huashu(str(self.tmp / "missing-huashu"))
        self.assertIn("no other installation", str(huashu_error.exception))

    def test_preflight_does_not_claim_a_model_call(self):
        import paths

        fake = self.tmp / "fake-dsh"
        fake.write_text("#!/bin/sh\nprintf '0.2.0-rc.2\\n'\n", encoding="utf-8")
        fake.chmod(0o755)
        report = paths.preflight(str(fake))
        expected = "passed" if report["platform_supported"] else "unsupported_platform"
        self.assertEqual(report["preflight"], expected)
        self.assertFalse(report["model_called"])
        self.assertEqual(report["model"]["model"], "deepseek-flash")
        self.assertEqual(report["skill_root"], str(SKILL.resolve()))
        self.assertTrue(Path(report["config"]).is_file())

    @POSIX_ONLY
    def test_preflight_fails_for_invalid_explicit_node_or_huashu(self):
        import paths

        fake = self.tmp / "fake-dsh"
        fake.write_text("#!/bin/sh\nprintf '0.2.0-rc.2\\n'\n", encoding="utf-8")
        fake.chmod(0o755)
        with mock.patch.dict(os.environ, {"NODE_BIN": str(self.tmp / "missing-node")}):
            report = paths.preflight(str(fake))
        self.assertEqual(report["preflight"], "failed")
        self.assertTrue(any("node:" in error for error in report["config_errors"]))

        with mock.patch.dict(os.environ, {"HUASHU_CHROME_ENTRY": str(self.tmp / "missing-huashu")}):
            report = paths.preflight(str(fake))
        self.assertEqual(report["preflight"], "failed")
        self.assertTrue(any("huashu:" in error for error in report["config_errors"]))

    def test_huashu_package_without_cli_is_rejected(self):
        import paths

        package = self.tmp / "huashu-no-cli"
        (package / "src").mkdir(parents=True)
        (package / "package.json").write_text(
            json.dumps({"name": "huashu-chrome", "version": "1.3.0"}), encoding="utf-8"
        )
        with self.assertRaises(FileNotFoundError):
            paths.resolve_huashu(str(package))
    @POSIX_ONLY
    def test_preflight_cli_exits_nonzero_for_explicit_config_error(self):
        fake = self.tmp / "fake-dsh"
        fake.write_text("#!/bin/sh\nprintf '0.2.0-rc.2\\n'\n", encoding="utf-8")
        fake.chmod(0o755)
        env = {**os.environ, "NODE_BIN": str(self.tmp / "missing-node"),
               "PYTHONDONTWRITEBYTECODE": "1"}
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "preflight.py"), "--dsh", str(fake)],
            capture_output=True, text=True, env=env, timeout=20,
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["preflight"], "failed")
        self.assertTrue(any("node:" in error for error in report["config_errors"]))
