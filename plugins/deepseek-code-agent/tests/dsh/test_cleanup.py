import json
import os
import signal
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from support import POSIX_ONLY, process_alive, wait_process_gone, wait_until

import worker


CHILD = r'''
import os
import signal
import sys
import time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1] + ".child", "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))
time.sleep(120)
'''

LEADER = r'''
import json
import os
import subprocess
import sys
import time
child = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump({"leader": os.getpid(), "child": child.pid}, handle)
if sys.argv[3] == "exit":
    sys.exit(0)
time.sleep(120)
'''


@POSIX_ONLY
class ProcessOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsh cleanup "))
        import shutil
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.child_script = self.tmp / "child.py"
        self.leader_script = self.tmp / "leader.py"
        self.child_script.write_text(CHILD, encoding="utf-8")
        self.leader_script.write_text(LEADER, encoding="utf-8")
        self.leaders = []

    def start_group(self, mode, token):
        pidfile = self.tmp / (mode + " pids.json")
        env = {**os.environ, "DSH_DELEGATE_OWNER": token}
        leader = subprocess.Popen(
            [sys.executable, str(self.leader_script), str(self.child_script), str(pidfile), mode],
            env=env, start_new_session=True,
        )
        self.leaders.append(leader)
        self.assertTrue(wait_until(lambda: pidfile.exists(), timeout=10))
        pids = json.loads(pidfile.read_text())
        self.assertTrue(wait_until(lambda: (self.tmp / (str(pidfile) + ".child")).exists(), timeout=10))
        return leader, pids

    def tearDown(self):
        # Every group here was created by this test with start_new_session=True,
        # so leader.pid is the private PGID. Kill the group even when the leader
        # has already exited, otherwise an ignore-TERM child can survive skip or
        # assertion paths for its full sleep.
        for leader in self.leaders:
            try:
                os.killpg(leader.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            if leader.poll() is None:
                try:
                    leader.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    leader.kill()
                    leader.wait(timeout=5)

    def test_unverifiable_pid_is_not_signaled(self):
        leader, pids = self.start_group("stay", "owner-token")
        try:
            self.assertFalse(worker.owned_group_alive({"child_pid": leader.pid}))
            self.assertFalse(worker.owned_group_alive({"child_pid": leader.pid, "owner_token": "wrong"}))
            self.assertFalse(worker.stop_owned_group({"child_pid": leader.pid, "owner_token": "wrong"}))
            self.assertTrue(process_alive(pids["child"]))
        finally:
            os.killpg(leader.pid, signal.SIGKILL)
            leader.wait(timeout=5)

    def test_valid_token_and_ps_unavailable_cleans_a_term_ignoring_child(self):
        leader, pids = self.start_group("stay", "right-token")
        active = {"child_pid": leader.pid, "owner_token": "right-token"}
        self.assertTrue(worker.owned_group_alive(active))
        reaper = threading.Thread(target=leader.wait)
        reaper.start()
        try:
            with mock.patch.object(worker, "_ps", return_value=None):
                self.assertTrue(worker.stop_owned_group(active))
            self.assertTrue(wait_process_gone(pids["child"]))
        finally:
            reaper.join(timeout=5)

    def test_leader_exit_with_trusted_popen_still_kills_term_ignoring_member(self):
        leader, pids = self.start_group("exit", "right-token")
        leader.wait(timeout=5)
        active = {"child_pid": leader.pid, "owner_token": "right-token"}
        self.assertTrue(worker.stop_owned_group(active, trusted=True))
        self.assertTrue(wait_process_gone(pids["child"]))

    def test_group_members_are_found_after_leader_exit_when_verification_is_available(self):
        leader, pids = self.start_group("exit", "right-token")
        leader.wait(timeout=5)
        # Linux can enumerate /proc; macOS uses ps when the sandbox permits it.
        members = worker.group_has_owner(leader.pid, "right-token")
        if members is None:
            self.skipTest("group membership cannot be read on this host")
        self.assertTrue(members)
        self.assertTrue(worker.stop_owned_group({"child_pid": leader.pid, "owner_token": "right-token"}))
        self.assertTrue(wait_process_gone(pids["child"]))
