import base64
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from support import SCRIPTS  # noqa: F401  (adds scripts to sys.path)

from agent_api import snapshot
from worker import FORMAT, save


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsh cursor "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / ("dsh-" + "a" * 32)
        for directory in ("inbox", "delivered", "outbox", "questions", "replies"):
            (self.root / "mailbox" / directory).mkdir(parents=True)
        (self.root / "turns").mkdir()
        save(self.root / "state.json", {
            "format": FORMAT,
            "state": "idle",
            "revision": 0,
            "session_id": "session-cursor",
            "queue": [],
            "next_id": 2,
            "completed_turns": 0,
            "active": None,
            "inflight": None,
            "controller_pid": None,
            "last_result": None,
            "last_event": None,
            "paused": False,
            "closed": False,
            "error": None,
        })

    def put(self, seq, message_id, text):
        message = {"id": message_id, "message": text}
        if seq is not None:
            message["seq"] = seq
        (self.root / "mailbox" / "outbox" / (message_id + ".json")).write_text(
            json.dumps(message), encoding="utf-8"
        )

    def test_late_lower_sequence_is_not_skipped(self):
        self.put(5, "m-000000000005-z", "five")
        self.put(6, "m-000000000006-a", "six")
        first = snapshot(self.root)
        self.assertEqual([message["message"] for message in first["messages"]], ["five", "six"])
        self.assertEqual([message["seq"] for message in first["messages"]], [5, 6])

        self.put(4, "m-000000000004-late", "late lower sequence")
        second = snapshot(self.root, first["cursor"])
        self.assertEqual([message["message"] for message in second["messages"]], ["late lower sequence"])
        third = snapshot(self.root, second["cursor"])
        self.assertEqual(third["messages"], [])

        self.put(7, "m-000000000007-000", "seven")
        fourth = snapshot(self.root, third["cursor"])
        self.assertEqual([message["message"] for message in fourth["messages"]], ["seven"])

    def test_sequence_not_lexical_id_orders_same_millisecond_and_clock_rollback(self):
        self.put(10, "m-000000000010-z", "ten")
        self.put(11, "m-000000000011-a", "eleven")
        first = snapshot(self.root)
        self.assertEqual([message["message"] for message in first["messages"]], ["ten", "eleven"])

        # Wall-clock timestamps and UUID suffixes decrease; sequence still wins.
        self.put(12, "m-000000000012-999", "twelve")
        self.put(13, "m-000000000013-000", "thirteen")
        second = snapshot(self.root, first["cursor"])
        self.assertEqual([message["message"] for message in second["messages"]], ["twelve", "thirteen"])

    def test_legacy_cursor_still_reads_old_records_and_accepts_new_sequence(self):
        self.put(None, "m-1000-a", "old one")
        self.put(None, "m-1001-b", "old two")
        legacy = base64.urlsafe_b64encode(json.dumps({"message": "m-1000-a", "result": None}).encode()).decode()
        first = snapshot(self.root, legacy)
        self.assertEqual([message["message"] for message in first["messages"]], ["old two"])

        # New sequenced IDs sort before the old timestamp IDs; the delivered set,
        # not the legacy lexical high-water, decides what is new.
        self.put(1, "m-000000000001-new", "new")
        second = snapshot(self.root, first["cursor"])
        self.assertEqual([message["message"] for message in second["messages"]], ["new"])
        third = snapshot(self.root, second["cursor"])
        self.assertEqual(third["messages"], [])

    def test_invalid_delivered_cursor_is_rejected(self):
        bad = base64.urlsafe_b64encode(json.dumps({"delivered": [1, 2]}).encode()).decode()
        with self.assertRaises(ValueError):
            snapshot(self.root, bad)
