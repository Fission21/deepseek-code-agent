import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

POSIX_ONLY = unittest.skipUnless(
    os.name == "posix",
    "DSH execution uses POSIX process groups and advisory locks; Windows preflight is tested separately.",
)
PLUGIN = Path(__file__).resolve().parents[2]
SKILL = PLUGIN / "skills" / "dsh-delegate"
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FAKE_DSH = r'''#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time

def emit(value):
    print(json.dumps(value), flush=True)

argv = sys.argv[1:]
session_arg = None
for index, arg in enumerate(argv):
    if arg == "--session-id" and index + 1 < len(argv):
        session_arg = argv[index + 1]
mode = os.environ.get("FAKE_DSH_MODE", "complete")
mode_file = os.environ.get("FAKE_DSH_MODE_FILE")
if mode_file:
    with open(mode_file, encoding="utf-8") as handle:
        mode = handle.read().strip()
log = os.environ.get("FAKE_DSH_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"mode": mode, "session": session_arg, "argv": argv}) + "\n")
session = session_arg or os.environ.get("FAKE_DSH_SESSION", "session-fake-0001")
emit({"type": "session", "sessionId": session})
emit({"type": "status", "phase": "thinking", "turn": 1, "step": 1,
      "usage": {"input_tokens": 11, "output_tokens": 7}})
emit({"type": "thinking", "text": "SECRET_THOUGHT_DO_NOT_LEAK"})
emit({"type": "tool_call", "tool": "shell",
      "arguments": {"cmd": "SECRET_RAW_ARG_DO_NOT_LEAK"}})
emit({"type": "tool_result", "status": "ok",
      "content": "SECRET_RAW_RESULT_DO_NOT_LEAK"})
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    pidfile = os.environ.get("FAKE_DSH_PIDFILE")
    if pidfile:
        with open(pidfile, "w", encoding="utf-8") as handle:
            json.dump({"dsh": os.getpid(), "child": child.pid}, handle)
    while True:
        time.sleep(1)
if mode == "fail":
    emit({"type": "status", "phase": "failed", "turn": 1, "step": 2})
    sys.exit(3)
emit({"type": "final", "text": "Completed fake task."})
'''


def install_fake(path):
    path.write_text(FAKE_DSH, encoding="utf-8")
    path.chmod(0o755)
    return path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def wait_until(predicate, timeout=10, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def wait_process_gone(pid, timeout=8):
    return wait_until(lambda: not process_alive(pid), timeout=timeout)


class DSHTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsh test "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workspace = self.tmp / "workspace with spaces [x]"
        (self.workspace / "src").mkdir(parents=True)
        (self.workspace / "AGENTS.md").write_text("# Project rules\nKeep scope.\n", encoding="utf-8")
        (self.workspace / "src" / "app.txt").write_text("sample\n", encoding="utf-8")
        self.fake = install_fake(self.tmp / "fake dsh")
        self.log = self.tmp / "calls.jsonl"
        self.pidfile = self.tmp / "fake-pids.json"
        self.mode_file = self.tmp / "fake-mode.txt"
        self.mode_file.write_text("complete", encoding="utf-8")
        self.root = self.tmp / "agent state"
        self.agents = []
        self.cursors = {}
        self.env = {
            "FAKE_DSH_LOG": str(self.log),
            "FAKE_DSH_MODE": "complete",
            "FAKE_DSH_MODE_FILE": str(self.mode_file),
            "FAKE_DSH_PIDFILE": str(self.pidfile),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self._old_env = {}
        for key, value in self.env.items():
            self._old_env[key] = os.environ.get(key)
            os.environ[key] = value

        from agent_api import AgentAPI
        self.api = AgentAPI(self.root)

    def tearDown(self):
        for agent_id in list(self.agents):
            try:
                self.api.call("close_agent", {"agent_id": agent_id})
            except Exception:
                pass
        # The shared scheduler inherits its environment once. Keep the mutable
        # fake mode in a fixture file, and let its final bookkeeping finish
        # before the fixture's registry directory is removed.
        if (self.root / "scheduler.sqlite3").exists():
            from scheduler import Scheduler, process_alive as scheduler_alive
            scheduler = Scheduler(self.root)
            def stopped():
                with scheduler.db() as db:
                    pid = db.execute("SELECT value FROM settings WHERE key='daemon_pid'").fetchone()[0]
                return not scheduler_alive(pid, self.root)
            self.assertTrue(wait_until(stopped, timeout=8), "Test scheduler did not stop")
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def set_mode(self, mode):
        os.environ["FAKE_DSH_MODE"] = mode
        self.mode_file.write_text(mode, encoding="utf-8")

    def spawn(self, mode="complete", timeout=20, **extra):
        self.set_mode(mode)
        params = {
            "task": "Implement the bounded test task; report changed files and checks.",
            "workspace": str(self.workspace),
            "scope_paths": ["src"],
            "dsh": str(self.fake),
            "timeout_seconds": timeout,
        }
        params.update(extra)
        result = self.api.call("spawn_agent", params)
        self.agents.append(result["agent_id"])
        return result

    def wait(self, agent_id, timeout=15):
        cursor = self.cursors.get(agent_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.api.call("wait_agent", {
                "agent_id": agent_id,
                "cursor": cursor,
                "timeout_seconds": min(5, max(0, deadline - time.monotonic())),
            })
            cursor = result.get("cursor")
            self.cursors[agent_id] = cursor
            if result.get("result") or result.get("state") in ("idle", "paused", "closed", "needs_attention"):
                return result
        self.fail("agent did not settle: %s" % agent_id)

    def state(self, agent_id):
        from worker import read_state
        return read_state(self.root / agent_id)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def wait_for_active(self, agent_id, timeout=10):
        self.assertTrue(wait_until(lambda: self.state(agent_id).get("active"), timeout=timeout))
        return self.state(agent_id)["active"]
