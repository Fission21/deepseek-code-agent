import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import unittest

from support import SCRIPTS, DSHTestCase

MCP = SCRIPTS / "mcp_server.py"


class MCPFramingTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="dsh mcp "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    def test_stdio_framing_tools_and_errors(self):
        requests = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "list_agents", "arguments": {}}}),
            "not-json",
            json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "unknown_tool", "arguments": {}}}),
        ]) + "\n"
        completed = subprocess.run(
            [sys.executable, str(MCP), "--root", str(self.tmp / "agents")],
            input=requests, capture_output=True, text=True, env=self.env, timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        by_id = {response.get("id"): response for response in responses}
        self.assertEqual(len(responses), 5)
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "dsh-agents")
        self.assertEqual(by_id[1]["result"]["protocolVersion"], "2025-06-18")
        names = {tool["name"] for tool in by_id[2]["result"]["tools"]}
        self.assertEqual(names, {
            "spawn_agent", "send_message", "wait_agent", "inspect_agent",
            "reply_agent", "interrupt_agent", "list_agents", "close_agent",
        })
        self.assertEqual(json.loads(by_id[3]["result"]["content"][0]["text"]), {"agents": []})
        self.assertEqual(by_id[None]["error"]["code"], -32700)
        self.assertEqual(by_id[4]["error"]["code"], -32602)

    def test_tool_schema_rejects_implicit_lane_confusion(self):
        import mcp_server

        tools = {tool["name"]: tool for tool in mcp_server.TOOLS}
        self.assertNotIn("ds_spawn_agent", tools)
        self.assertEqual(tools["spawn_agent"]["inputSchema"]["required"], ["task", "workspace"])
        self.assertEqual(tools["wait_agent"]["inputSchema"]["properties"]["timeout_seconds"]["maximum"], 55)
        self.assertEqual(tools["spawn_agent"]["inputSchema"]["properties"]["task"]["maxLength"], 12000)


@unittest.skipUnless(os.name == "posix", "DSH execution is POSIX-only")
class MCPLifecycleTests(DSHTestCase):
    def setUp(self):
        super().setUp()
        self.server = subprocess.Popen(
            [sys.executable, str(MCP), "--root", str(self.root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        if self.server.poll() is None:
            self.server.kill()
            self.server.wait(timeout=5)
        for stream in (self.server.stdin, self.server.stdout, self.server.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def request(self, payload, timeout=15):
        self.server.stdin.write(json.dumps(payload) + "\n")
        self.server.stdin.flush()
        ready, _, _ = select.select([self.server.stdout], [], [], timeout)
        if not ready:
            self.fail("MCP server did not answer within %ss" % timeout)
        line = self.server.stdout.readline()
        return json.loads(line)

    def call(self, name, arguments, request_id):
        return self.request({
            "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })

    def test_mcp_can_spawn_wait_and_close_the_real_controller(self):
        spawned = self.call("spawn_agent", {
            "task": "Complete the fake MCP task.",
            "workspace": str(self.workspace),
            "dsh": str(self.fake),
            "scope_paths": ["src"],
        }, 1)
        result = json.loads(spawned["result"]["content"][0]["text"])
        agent_id = result["agent_id"]
        self.agents.append(agent_id)
        waited = self.call("wait_agent", {"agent_id": agent_id, "timeout_seconds": 10}, 2)
        payload = json.loads(waited["result"]["content"][0]["text"])
        self.assertEqual(payload["result"]["status"], "completed")
        closed = self.call("close_agent", {"agent_id": agent_id}, 3)
        closed_payload = json.loads(closed["result"]["content"][0]["text"])
        self.assertEqual(closed_payload["state"], "closed")
