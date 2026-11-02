import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from support import SCRIPTS  # noqa: F401  (adds scripts to sys.path)

import context_packet


class ContextPacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsh context "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workspace = self.tmp / "workspace"
        (self.workspace / "nested").mkdir(parents=True)
        (self.workspace / "AGENTS.md").write_text("# Root rules\n", encoding="utf-8")
        (self.workspace / "nested" / "AGENTS.md").write_text("# Nested rules\n", encoding="utf-8")
        self.context = self.workspace / "notes.md"
        self.context.write_text("selected context\n", encoding="utf-8")

    def test_selected_context_is_hashed_and_bounded(self):
        meta, text = context_packet.read_selected(str(self.context))
        self.assertEqual(text, "selected context\n")
        self.assertEqual(meta["sha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(meta["bytes"], len(text.encode()))

        oversized = self.workspace / "too-large.md"
        oversized.write_text("x" * (context_packet.MAX_FILE + 1), encoding="utf-8")
        with self.assertRaises(ValueError):
            context_packet.read_selected(str(oversized))

        with self.assertRaises(ValueError):
            context_packet.read_selected(str(self.workspace / "image.png"))

    def test_credential_and_internal_state_directories_are_rejected(self):
        for relative in (".ssh/notes.md", ".aws/notes.md", ".codex/sessions/notes.md"):
            target = self.tmp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("secret\n", encoding="utf-8")
            with self.assertRaises(ValueError, msg=relative):
                context_packet.read_selected(str(target))

        fake_credentials = self.workspace / "credentials.md"
        fake_credentials.write_text("secret\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            context_packet.read_selected(str(fake_credentials))

    def test_write_scope_keeps_exact_workspace_paths(self):
        (self.workspace / "dir with space").mkdir()
        self.assertEqual(
            context_packet.write_scope(self.workspace, ["src", "dir with space"]),
            ["src", "dir with space"],
        )
        for bad in ("../outside", str(self.workspace / "src"), "", "src/*"):
            with self.assertRaises(ValueError, msg=bad):
                context_packet.write_scope(self.workspace, [bad])

        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
        link = self.workspace / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this host")
        with self.assertRaises(ValueError):
            context_packet.write_scope(self.workspace, ["escape"])

    def test_packet_includes_selected_sources_and_not_unrelated_files(self):
        config = {
            "cwd": str(self.workspace),
            "allow_write": ["nested"],
            "rule_files": [],
            "context_files": [str(self.context)],
            "native_rules": False,
        }
        body, sources = context_packet.packet(config, {"id": 1, "text": "do the work", "context_files": []})
        self.assertIn("CONTROLLER TASK PACKET", body)
        self.assertIn("CURRENT MESSAGE 1", body)
        self.assertIn(str(self.context.resolve()), body)
        self.assertIn(str((self.workspace / "nested" / "AGENTS.md").resolve()), body)
        self.assertIn("Root rules", body)
        self.assertNotIn("unrelated", body)
        paths = {item["path"] for item in sources}
        self.assertIn(str(self.context.resolve()), paths)
        self.assertIn(str((self.workspace / "nested" / "AGENTS.md").resolve()), paths)

    def test_packet_rejects_overall_budget(self):
        big = self.workspace / "big.md"
        big.write_text("a" * (context_packet.MAX_FILE - 10), encoding="utf-8")
        config = {
            "cwd": str(self.workspace),
            "allow_write": [],
            "rule_files": [],
            "context_files": [str(big)],
            "native_rules": True,
        }
        with self.assertRaises(ValueError):
            context_packet.packet(config, {"id": 1, "text": "b" * (context_packet.MAX_PACKET - 100), "context_files": []})
