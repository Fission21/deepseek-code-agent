import json
import os
import signal
import time
import unittest

from support import POSIX_ONLY, DSHTestCase, read_json, wait_until, wait_process_gone


@POSIX_ONLY
class WorkerAPITests(DSHTestCase):
    def test_spawn_completion_filters_output_and_cursor_deduplicates(self):
        spawned = self.spawn()
        agent_id = spawned["agent_id"]
        settled = self.wait(agent_id)
        self.assertEqual(settled["result"]["status"], "completed")
        self.assertEqual(settled["result"]["text"], "Completed fake task.")
        self.assertTrue(wait_until(lambda: self.state(agent_id)["state"] == "idle"))
        self.assertEqual(self.state(agent_id)["completed_turns"], 1)
        self.assertEqual(self.state(agent_id)["session_id"], "session-fake-0001")

        report = read_json(settled["result"]["report_file"])
        self.assertEqual(report["final"], "Completed fake task.")
        self.assertNotIn("SECRET_THOUGHT", json.dumps(report))
        self.assertNotIn("SECRET_RAW_ARG", json.dumps(report))
        self.assertNotIn("SECRET_RAW_RESULT", json.dumps(report))

        repeated = self.api.call("inspect_agent", {"agent_id": agent_id, "cursor": settled["cursor"]})
        self.assertEqual(repeated["messages"], [])
        self.assertNotIn("result", repeated)

        listed = self.api.call("list_agents", {})
        self.assertEqual(len(listed["agents"]), 1)
        self.assertEqual(listed["agents"][0]["agent_id"], agent_id)
        self.assertEqual(listed["agents"][0]["title"], "")

    def test_send_message_resumes_the_same_session(self):
        spawned = self.spawn()
        agent_id = spawned["agent_id"]
        self.wait(agent_id)
        delivered = self.api.call("send_message", {"agent_id": agent_id, "message": "A bounded correction."})
        self.assertEqual(delivered["delivery"], "resumed_session")
        settled = self.wait(agent_id)
        self.assertEqual(settled["result"]["status"], "completed")
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0]["session"])
        self.assertEqual(calls[1]["session"], "session-fake-0001")
        self.assertEqual(self.state(agent_id)["session_id"], "session-fake-0001")

    def test_timeout_pauses_and_resume_does_not_replay(self):
        spawned = self.spawn(mode="hang", timeout=3)
        agent_id = spawned["agent_id"]
        self.assertTrue(wait_until(lambda: self.state(agent_id)["state"] == "paused", timeout=12))
        settled = self.wait(agent_id, timeout=10)
        self.assertEqual(settled["result"]["status"], "timeout")
        self.assertIn("did not complete", self.state(agent_id)["error"])
        self.assertEqual(len(self.calls()), 1)
        pids = read_json(self.pidfile)
        self.assertTrue(wait_process_gone(pids["dsh"]))
        self.assertTrue(wait_process_gone(pids["child"]))

        self.set_mode("complete")
        delivered = self.api.call("send_message", {"agent_id": agent_id, "message": "Resume with the correction."})
        self.assertEqual(delivered["delivery"], "resumed_session")
        settled = self.wait(agent_id)
        self.assertEqual(settled["result"]["status"], "completed")
        self.assertTrue(wait_until(lambda: self.state(agent_id)["state"] == "idle"))
        self.assertEqual(self.state(agent_id)["completed_turns"], 1)
        self.assertEqual(len(self.calls()), 2)

    def test_interrupt_cleans_group_keeps_queue_and_never_replays_inflight(self):
        spawned = self.spawn(mode="hang")
        agent_id = spawned["agent_id"]
        active = self.wait_for_active(agent_id)
        self.assertTrue(wait_until(lambda: (self.state(agent_id)["active"] or {}).get("child_pid") is not None))
        self.assertTrue(wait_until(lambda: self.pidfile.exists()))
        pids = read_json(self.pidfile)
        queued = self.api.call("send_message", {"agent_id": agent_id, "message": "Queued follow-up."})
        self.assertEqual(queued["delivery"], "next_model_boundary")
        inbox = list((self.root / agent_id / "mailbox" / "inbox").glob("*.json"))
        self.assertEqual(len(inbox), 1)
        self.assertEqual(json.loads(inbox[0].read_text())["message"], "Queued follow-up.")

        interrupted = self.api.call("interrupt_agent", {"agent_id": agent_id})
        self.assertEqual(interrupted["state"], "paused")
        self.assertIsNone(self.state(agent_id)["active"])
        self.assertTrue(wait_process_gone(pids["dsh"]))
        self.assertTrue(wait_process_gone(pids["child"]))
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(len(list((self.root / agent_id / "mailbox" / "inbox").glob("*.json"))), 1)

        self.set_mode("complete")
        self.api.call("send_message", {"agent_id": agent_id, "message": "Correction after interrupt."})
        self.assertTrue(wait_until(
            lambda: self.state(agent_id)["completed_turns"] == 2 and self.state(agent_id)["state"] == "idle",
            timeout=15,
        ))
        calls = self.calls()
        self.assertEqual([call["mode"] for call in calls], ["hang", "complete", "complete"])
        self.assertEqual(calls[1]["session"], "session-fake-0001")
        self.assertEqual(calls[2]["session"], "session-fake-0001")

    def test_pending_question_answer_is_bound_to_the_active_execution(self):
        spawned = self.spawn(mode="hang")
        agent_id = spawned["agent_id"]
        active = self.wait_for_active(agent_id)
        request_id = "q-" + "a" * 32
        question = {
            "id": request_id,
            "question": "Choose a bounded option.",
            "status": "pending",
            "at": int(time.time() * 1000),
            "invocation": active["result_file"],
        }
        question_path = self.root / agent_id / "mailbox" / "questions" / (request_id + ".json")
        question_path.write_text(json.dumps(question), encoding="utf-8")

        inspected = self.api.call("inspect_agent", {"agent_id": agent_id})
        self.assertEqual(inspected["state"], "waiting_for_parent")
        self.assertEqual(len(inspected["pending_questions"]), 1)

        first = self.api.call("reply_agent", {"agent_id": agent_id, "request_id": request_id, "answer": "Option A."})
        self.assertEqual(first, {"replied": request_id})
        replay = self.api.call("reply_agent", {"agent_id": agent_id, "request_id": request_id, "answer": "Option A."})
        self.assertTrue(replay["replayed"])
        with self.assertRaises(ValueError):
            self.api.call("reply_agent", {"agent_id": agent_id, "request_id": request_id, "answer": "Option B."})
        self.api.call("interrupt_agent", {"agent_id": agent_id})

    def test_failure_pauses_without_replay_and_can_be_corrected(self):
        spawned = self.spawn(mode="fail")
        agent_id = spawned["agent_id"]
        settled = self.wait(agent_id, timeout=10)
        self.assertEqual(settled["result"]["status"], "incomplete")
        self.assertEqual(self.state(agent_id)["state"], "paused")
        self.assertEqual(len(self.calls()), 1)
        self.set_mode("complete")
        self.api.call("send_message", {"agent_id": agent_id, "message": "Try the corrected path."})
        settled = self.wait(agent_id, timeout=10)
        self.assertEqual(settled["result"]["status"], "completed")
        self.assertEqual(len(self.calls()), 2)

    def test_close_blocks_new_work_and_stops_the_process_group(self):
        spawned = self.spawn(mode="hang")
        agent_id = spawned["agent_id"]
        active = self.wait_for_active(agent_id)
        self.assertTrue(wait_until(lambda: (self.state(agent_id)["active"] or {}).get("child_pid") is not None))
        self.assertTrue(wait_until(lambda: self.pidfile.exists()))
        pids = read_json(self.pidfile)
        closed = self.api.call("close_agent", {"agent_id": agent_id})
        self.assertEqual(closed["state"], "closed")
        self.assertTrue(wait_process_gone(pids["dsh"]))
        self.assertTrue(wait_process_gone(pids["child"]))
        with self.assertRaises(ValueError):
            self.api.call("send_message", {"agent_id": agent_id, "message": "Too late."})

    def test_killed_launcher_does_not_leave_the_child_group_running(self):
        spawned = self.spawn(mode="hang")
        agent_id = spawned["agent_id"]
        self.wait_for_active(agent_id)
        self.assertTrue(wait_until(lambda: (self.state(agent_id)["active"] or {}).get("child_pid") is not None))
        self.assertTrue(wait_until(lambda: self.pidfile.exists()))
        pids = read_json(self.pidfile)
        runner_pid = self.state(agent_id)["active"]["runner_pid"]
        self.assertIsInstance(runner_pid, int)
        started = time.monotonic()
        os.kill(runner_pid, signal.SIGKILL)
        self.assertTrue(wait_process_gone(pids["child"], timeout=6))
        self.api.call("close_agent", {"agent_id": agent_id})
        self.assertEqual(self.state(agent_id)["state"], "closed")
        # The grandchild inherits stdout; cleanup must not wait on the reader lock.
        self.assertLess(time.monotonic() - started, 10)

    def test_scope_escape_is_rejected_before_state_creation(self):
        with self.assertRaises(ValueError):
            self.api.call("spawn_agent", {
                "task": "No escape.",
                "workspace": str(self.workspace),
                "scope_paths": ["../outside"],
                "dsh": str(self.fake),
            })
        self.assertEqual(self.api.call("list_agents", {})["agents"], [])

    def test_relative_workspace_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api.call("spawn_agent", {
                "task": "No relative workspace.",
                "workspace": "relative/path",
                "dsh": str(self.fake),
            })
    def test_browser_pair_requires_a_safe_http_url(self):
        with self.assertRaises(ValueError):
            self.api.call("spawn_agent", {
                "task": "Browser task.",
                "workspace": str(self.workspace),
                "dsh": str(self.fake),
                "tab_id": 1,
                "url": "file:///private/page",
            })
        with self.assertRaises(ValueError):
            self.api.call("spawn_agent", {
                "task": "Browser task.",
                "workspace": str(self.workspace),
                "dsh": str(self.fake),
                "tab_id": 1,
                "url": "https://user:secret@example.invalid/page",
            })
        self.assertEqual(self.api.call("list_agents", {})["agents"], [])
