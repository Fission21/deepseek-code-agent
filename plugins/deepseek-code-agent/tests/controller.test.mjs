import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  buildInitialTask,
  commandInvocation,
  compactMessages,
  discoverInstructionManifest,
  latestMessageID,
  parseServerURL,
} from "../src/controller.mjs";
import { tools } from "../server.mjs";

test("parseServerURL accepts only the loopback OpenCode address", () => {
  assert.equal(
    parseServerURL("server listening on http://127.0.0.1:43210"),
    "http://127.0.0.1:43210",
  );
  assert.equal(parseServerURL("server listening on http://0.0.0.0:43210"), null);
});

test("command invocation runs Windows command shims through cmd.exe", () => {
  assert.deepEqual(
    commandInvocation(
      "C:\\Program Files\\OpenCode\\opencode.cmd",
      ["--version"],
      "win32",
      { ComSpec: "C:\\Windows\\System32\\cmd.exe" },
    ),
    {
      command: "C:\\Windows\\System32\\cmd.exe",
      args: [
        "/d",
        "/s",
        "/c",
        '"C:\\Program Files\\OpenCode\\opencode.cmd"',
        "--version",
      ],
    },
  );
  assert.deepEqual(commandInvocation("opencode", ["--version"], "linux"), {
    command: "opencode",
    args: ["--version"],
  });
});

test("compactMessages returns only messages after the cursor", () => {
  const messages = [
    {
      info: { id: "msg1", role: "user", time: { created: 1 } },
      parts: [{ type: "text", text: "task" }],
    },
    {
      info: {
        id: "msg2",
        role: "assistant",
        time: { created: 2, completed: 3 },
        cost: 0.1,
        tokens: { input: 2, output: 3, reasoning: 0, cache: { read: 1, write: 0 } },
      },
      parts: [
        { type: "text", text: "done" },
        { type: "patch", hash: "abc", files: ["a.py"] },
      ],
    },
  ];
  assert.equal(latestMessageID(messages), "msg2");
  assert.deepEqual(compactMessages(messages, "msg1"), {
    cursor: "msg2",
    messages: [
      {
        message_id: "msg2",
        role: "assistant",
        created_at: 2,
        completed_at: 3,
        error: null,
        cost: 0.1,
        tokens: { input: 2, output: 3, reasoning: 0, cache: { read: 1, write: 0 } },
        parts: [
          { type: "text", text: "done" },
          { type: "patch", hash: "abc", files: ["a.py"] },
        ],
      },
    ],
  });
});

test("instruction manifest discovers applicable AGENTS files and selected rules", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "deepseek-instructions-"));
  t.after(async () => await fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, "app", "services"), { recursive: true });
  await fs.mkdir(path.join(root, "docs", "rules"), { recursive: true });
  await fs.writeFile(path.join(root, "AGENTS.md"), "root rules\n");
  await fs.writeFile(path.join(root, "app", "AGENTS.md"), "ignored by override\n");
  await fs.writeFile(path.join(root, "app", "AGENTS.override.md"), "app override\n");
  await fs.writeFile(path.join(root, "docs", "rules", "coding.md"), "coding rules\n");

  const manifest = await discoverInstructionManifest({
    workspaceRoot: root,
    scopePaths: ["app/services/example.py"],
    requiredReads: [
      { path: "docs/rules/coding.md", sections: ["Types", "Error handling"] },
      "AGENTS.md",
    ],
  });

  assert.deepEqual(
    manifest.files.map((file) => file.path),
    ["AGENTS.md", "app/AGENTS.override.md", "docs/rules/coding.md"],
  );
  assert.deepEqual(manifest.files[0].sources, ["applicable_agents", "required_read"]);
  assert.equal(
    manifest.files[2].sha256,
    createHash("sha256").update("coding rules\n").digest("hex"),
  );
  assert.deepEqual(manifest.files[2].sections, ["Types", "Error handling"]);
  assert.equal(
    manifest.total_bytes,
    Buffer.byteLength("root rules\napp override\ncoding rules\n"),
  );
});

test("instruction manifest rejects paths outside the workspace", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "deepseek-instructions-"));
  t.after(async () => await fs.rm(root, { recursive: true, force: true }));
  await assert.rejects(
    discoverInstructionManifest({ workspaceRoot: root, requiredReads: ["../AGENTS.md"] }),
    /stay inside the workspace/,
  );
});

test("initial task carries compact constraints and manifest hashes without file contents", () => {
  const text = buildInitialTask({
    task: "Implement the bounded change.",
    scopePaths: ["app/service.py"],
    criticalConstraints: ["Do not change the API contract."],
    instructionManifest: {
      files: [{ path: "AGENTS.md", sha256: "abc123", bytes: 42, sections: [] }],
      total_bytes: 42,
    },
  });
  assert.match(text, /app\/service\.py/);
  assert.match(text, /Do not change the API contract/);
  assert.match(text, /AGENTS\.md \| sha256=abc123 \| bytes=42 \| read=full/);
  assert.doesNotMatch(text, /root rules/);
});

test("the MCP surface contains the complete controller lifecycle", () => {
  const names = new Set(tools.map((tool) => tool.name));
  for (const required of [
    "ds_check",
    "ds_spawn_agent",
    "ds_send_message",
    "ds_wait_agent",
    "ds_inspect_agent",
    "ds_fork_agent",
    "ds_interrupt_agent",
    "ds_reply_agent",
    "ds_list_agents",
    "ds_close_agent",
  ]) {
    assert.ok(names.has(required), `missing ${required}`);
  }
});

test("ds_spawn_agent exposes scoped instruction routing", () => {
  const spawnTool = tools.find((tool) => tool.name === "ds_spawn_agent");
  assert.ok(spawnTool.inputSchema.properties.scope_paths);
  assert.ok(spawnTool.inputSchema.properties.required_reads);
  assert.ok(spawnTool.inputSchema.properties.critical_constraints);
});
