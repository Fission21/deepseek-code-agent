import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import {
  DeepSeekController,
  WORKER_SYSTEM_PROMPT,
  aggregateUsage,
  boundReport,
  buildInitialTask,
  commandInvocation,
  compactMessages,
  discoverInstructionManifest,
  encodeCursor,
  latestMessageID,
  messageRevision,
  parseCursor,
  parseServerURL,
  resolveEffectiveSelection,
  resolveModelSelection,
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

test("wait and inspect default to compact with a full escape hatch", () => {
  for (const name of ["ds_wait_agent", "ds_inspect_agent"]) {
    const tool = tools.find((entry) => entry.name === name);
    assert.deepEqual(tool.inputSchema.properties.detail.enum, ["compact", "full"]);
    assert.equal(tool.inputSchema.properties.detail.default, "compact");
    assert.deepEqual(tool.inputSchema.properties.cursor.type, ["string", "null"]);
    assert.equal(tool.inputSchema.properties.cursor.oneOf, undefined);
  }
});

test("worker handoff prompt is bounded and names the required report fields", () => {
  assert.match(WORKER_SYSTEM_PROMPT, /1500 characters/);
  for (const field of ["outcome", "changed files", "checks actually run", "risks", "manifest"]) {
    assert.match(WORKER_SYSTEM_PROMPT, new RegExp(field, "i"));
  }
  assert.match(WORKER_SYSTEM_PROMPT, /do not narrate/i);
});

test("cursor parsing round-trips revisions and accepts legacy message IDs", () => {
  const revision = "0123456789abcdef01234567";
  assert.deepEqual(parseCursor(`${"msg_1"}@${revision}`), {
    message_id: "msg_1",
    revision,
  });
  assert.deepEqual(parseCursor("msg_legacy"), { message_id: "msg_legacy", revision: null });
  assert.deepEqual(parseCursor({ message_id: "msg_2", revision }), {
    message_id: "msg_2",
    revision,
  });
  assert.equal(
    encodeCursor({ message_id: "msg_2", revision }),
    `msg_2@${revision}`,
  );
  assert.equal(encodeCursor("msg_legacy"), "msg_legacy");
  assert.equal(encodeCursor(null), null);
  assert.equal(parseCursor(null), null);
});

test("messageRevision changes when a streamed message completes with identical text", () => {
  const partial = {
    info: { id: "msg_a", role: "assistant", time: { created: 1 } },
    parts: [{ type: "text", text: "same text" }],
  };
  const completed = {
    info: { id: "msg_a", role: "assistant", time: { created: 1, completed: 2 } },
    parts: [{ type: "text", text: "same text" }],
  };
  assert.notEqual(messageRevision(partial), messageRevision(completed));
  assert.equal(
    messageRevision(partial),
    messageRevision(JSON.parse(JSON.stringify(partial))),
  );
});

test("usage aggregates message info and never double counts step-finish parts", () => {
  const stepFinish = (tokens, cost) => ({ type: "step-finish", reason: "stop", cost, tokens });
  const firstTokens = {
    input: 100,
    output: 50,
    reasoning: 10,
    cache: { read: 5, write: 2 },
    total: 165,
  };
  const messages = [
    userMessage(),
    assistantMessage({
      text: "one",
      cost: 0.25,
      tokens: firstTokens,
      parts: [stepFinish(firstTokens, 0.25)],
    }),
    assistantMessage({
      id: "msg_assistant_2",
      text: "two",
      parts: [stepFinish({ input: 7, output: 3, cache: { read: 1, write: 1 }, total: 10 }, 0.1)],
    }),
    assistantMessage({ id: "msg_assistant_3", text: "three", cost: 0.05, tokens: {} }),
  ];
  const usage = aggregateUsage(messages);
  assert.ok(Math.abs(usage.cost_usd - 0.4) < 1e-9);
  assert.deepEqual(usage.tokens, {
    input: 107,
    output: 53,
    reasoning: 10,
    cache_read: 6,
    cache_write: 3,
    total: 175,
  });
  assert.equal(usage.assistant_messages, 3);
  assert.equal(usage.messages_with_cost, 3);
  assert.equal(usage.messages_with_tokens, 2);
  assert.equal(usage.complete, false);
  assert.match(usage.note, /token usage unavailable for 1 of 3/);
  assert.match(usage.source, /step_finish_fallback/);

  const onlyCost = aggregateUsage([
    assistantMessage({ text: "cost only", cost: 0.2, tokens: {} }),
  ]);
  assert.equal(onlyCost.messages_with_tokens, 0);
  assert.equal(onlyCost.tokens, null);
  assert.equal(onlyCost.tokens_available, false);
  assert.equal(onlyCost.cost_available, true);
  assert.equal(onlyCost.complete, false);

  const missingFields = aggregateUsage([
    assistantMessage({ text: "total only", cost: 0.1, tokens: { total: 9 } }),
  ]);
  assert.deepEqual(missingFields.tokens, {
    input: null,
    output: null,
    reasoning: null,
    cache_read: null,
    cache_write: null,
    total: 9,
  });
  assert.equal(missingFields.messages_with_tokens, 1);
  assert.match(missingFields.note, /token fields unavailable/);

  const empty = aggregateUsage([]);
  assert.equal(empty.cost_usd, null);
  assert.equal(empty.tokens, null);
  assert.equal(empty.complete, false);
  assert.match(empty.note, /no assistant messages/);
});

test("boundReport enforces the hard limit and marks truncation", () => {
  const bounded = boundReport("x".repeat(5000));
  assert.equal(bounded.truncated, true);
  assert.ok(bounded.text.length <= 3000);
  assert.match(bounded.text, /truncated/);
  const short = boundReport("small report");
  assert.deepEqual(short, { text: "small report", truncated: false });
  assert.deepEqual(boundReport(""), { text: null, truncated: false });
});

const OFFLINE_AGENT_ID = "ses_offline_agent";

function jsonResponse(payload) {
  return {
    ok: true,
    status: 200,
    async text() {
      return JSON.stringify(payload);
    },
  };
}

function userMessage(id = "msg_user", text = "Please implement the change.") {
  return {
    info: { id, role: "user", time: { created: 1 } },
    parts: [{ type: "text", text }],
  };
}

function assistantMessage({
  id = "msg_assistant",
  text = "done",
  completed,
  cost,
  tokens,
  error,
  parts = [],
} = {}) {
  const info = { id, role: "assistant", time: { created: 1 } };
  if (completed !== null) info.time.completed = completed ?? 2;
  if (cost !== undefined) info.cost = cost;
  if (tokens !== undefined) info.tokens = tokens;
  if (error !== undefined) info.error = error;
  return { info, parts: [{ type: "text", text }, ...parts] };
}

async function offlineController(t, options) {
  const stateRoot = await fs.mkdtemp(path.join(os.tmpdir(), "deepseek-controller-"));
  t.after(async () => {
    await fs.rm(stateRoot, { recursive: true, force: true });
  });
  const controller = new DeepSeekController({
    stateRoot,
    spawn: () => {
      throw new Error("spawn must not be called in offline tests");
    },
    fetch: async (url) => {
      const parsed = new URL(url);
      if (parsed.pathname === "/session/status") {
        return jsonResponse({ [OFFLINE_AGENT_ID]: options.status?.() ?? { type: "idle" } });
      }
      if (parsed.pathname === `/api/session/${OFFLINE_AGENT_ID}/permission`) {
        return jsonResponse(options.permissions?.() ?? []);
      }
      if (parsed.pathname === `/api/session/${OFFLINE_AGENT_ID}/question`) {
        return jsonResponse(options.questions?.() ?? []);
      }
      if (parsed.pathname === `/session/${OFFLINE_AGENT_ID}/message`) {
        return jsonResponse(options.messages());
      }
      throw new Error(`Unexpected offline request: ${parsed.pathname}`);
    },
  });
  controller.server = { url: "http://127.0.0.1:4096", authorization: "Basic test", child: null };
  await controller.writeState({
    agent_id: OFFLINE_AGENT_ID,
    status: "active",
    directory: path.join(stateRoot, "workspace"),
    source_root: path.join(stateRoot, "source"),
    workspace_mode: "worktree",
    temp_root: null,
    owns_worktree: false,
    created_at: 1,
    title: "offline",
    scope_paths: [],
    instruction_manifest: {
      files: [{ path: "AGENTS.md", sha256: "abc123", bytes: 10, sources: [], sections: [] }],
      total_bytes: 10,
    },
  });
  return controller;
}

test("same-ID streaming completion is captured without replay", async (t) => {
  let text = "partial answer";
  let completed = null;
  const controller = await offlineController(t, {
    messages: () => [userMessage(), assistantMessage({ text, completed })],
  });

  const timedOut = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, timeout_ms: 0 });
  assert.equal(timedOut.state, "timed_out");
  assert.equal(timedOut.final_report, null);
  assert.equal(timedOut.detail, "compact");

  const unchanged = await controller.inspectAgent({
    agent_id: OFFLINE_AGENT_ID,
    cursor: timedOut.cursor,
  });
  assert.equal(unchanged.state, "idle");
  assert.equal(unchanged.final_report, null);

  text = "partial answer plus the final report";
  completed = 9;
  const done = await controller.waitAgent({
    agent_id: OFFLINE_AGENT_ID,
    cursor: timedOut.cursor,
    timeout_ms: 0,
  });
  assert.equal(done.state, "completed");
  assert.match(done.final_report, /final report/);

  const repeat = await controller.inspectAgent({
    agent_id: OFFLINE_AGENT_ID,
    cursor: done.cursor,
  });
  assert.equal(repeat.state, "idle");
  assert.equal(repeat.final_report, null);
});

test("repeated timeouts with the same cursor do not replay and legacy strings still work", async (t) => {
  const controller = await offlineController(t, {
    messages: () => [userMessage(), assistantMessage({ text: "working", completed: null })],
  });

  const first = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, timeout_ms: 0 });
  const second = await controller.waitAgent({
    agent_id: OFFLINE_AGENT_ID,
    cursor: first.cursor,
    timeout_ms: 0,
  });
  assert.equal(first.state, "timed_out");
  assert.equal(second.state, "timed_out");
  assert.equal(second.final_report, null);

  const legacy = await controller.inspectAgent({
    agent_id: OFFLINE_AGENT_ID,
    cursor: "msg_assistant",
  });
  assert.equal(legacy.state, "idle");
  assert.equal(legacy.final_report, null);

  const legacyBefore = await controller.inspectAgent({
    agent_id: OFFLINE_AGENT_ID,
    cursor: "msg_user",
  });
  assert.equal(legacyBefore.state, "idle");
});

test("idle sessions with only user text are not completed", async (t) => {
  const controller = await offlineController(t, { messages: () => [userMessage()] });

  const wait = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, timeout_ms: 0 });
  assert.equal(wait.state, "timed_out");
  assert.equal(wait.final_report, null);

  const inspected = await controller.inspectAgent({ agent_id: OFFLINE_AGENT_ID });
  assert.equal(inspected.state, "idle");
  assert.equal(inspected.final_report, null);
});

test("failed assistant turns are reported as failures, never completed", async (t) => {
  const controller = await offlineController(t, {
    messages: () => [
      userMessage(),
      assistantMessage({
        text: "partial work",
        completed: 5,
        error: { name: "ProviderAuthError", data: { message: "no credentials" } },
      }),
    ],
  });

  const wait = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, timeout_ms: 0 });
  assert.equal(wait.state, "failed");
  assert.equal(wait.final_report, null);
  assert.equal(wait.error.name, "ProviderAuthError");
  assert.equal(wait.error.message, "no credentials");

  const repeat = await controller.inspectAgent({
    agent_id: OFFLINE_AGENT_ID,
    cursor: wait.cursor,
  });
  assert.equal(repeat.state, "idle");
  assert.equal(repeat.final_report, null);
});

test("pending permissions and questions surface as needs_attention", async (t) => {
  const controller = await offlineController(t, {
    messages: () => [userMessage()],
    permissions: () => [{ id: "per_123", type: "permission" }],
    questions: () => [{ id: "que_123", type: "question" }],
  });

  const wait = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, timeout_ms: 0 });
  assert.equal(wait.state, "needs_attention");
  assert.equal(wait.pending.permissions.length, 1);
  assert.equal(wait.pending.questions.length, 1);
  assert.equal(wait.final_report, null);
});

test("compact hides prompts, intermediates and manifests while full recovers the report", async (t) => {
  const longReport = `${"R".repeat(6000)}REPORT_TAIL`;
  const intermediate = "INTERMEDIATE_".repeat(3000);
  const controller = await offlineController(t, {
    messages: () => [
      userMessage("msg_user", "TASK_ECHO_SHOULD_BE_HIDDEN"),
      assistantMessage({
        id: "msg_mid",
        text: intermediate,
        completed: 3,
        parts: [{ type: "tool", tool: "bash", state: { status: "completed", title: "ran checks" } }],
      }),
      assistantMessage({ id: "msg_final", text: longReport, completed: 4 }),
    ],
  });

  const compact = await controller.inspectAgent({ agent_id: OFFLINE_AGENT_ID });
  assert.equal(compact.state, "completed");
  assert.equal(compact.report_truncated, true);
  assert.ok(compact.final_report.length <= 3000);
  assert.match(compact.final_report, /truncated/);
  assert.match(compact.report_hint, /detail=full/);
  assert.doesNotMatch(compact.final_report, /REPORT_TAIL/);
  assert.ok(!("instruction_manifest" in compact));
  assert.equal(typeof compact.cursor, "string");
  const compactText = JSON.stringify(compact);
  assert.ok(!compactText.includes("TASK_ECHO_SHOULD_BE_HIDDEN"));
  assert.ok(!compactText.includes("INTERMEDIATE_"));
  assert.ok(compactText.length < 8000);

  const full = await controller.inspectAgent({
    agent_id: OFFLINE_AGENT_ID,
    detail: "full",
  });
  assert.equal(full.state, "completed");
  assert.equal(full.final_report, longReport);
  assert.equal(full.messages.length, 3);
  assert.equal(full.instruction_manifest.files.length, 1);
  assert.equal(typeof full.cursor, "string");
  assert.ok(!("next_cursor" in full));
  assert.ok(!("cursor_message_id" in full));
  const fullText = JSON.stringify(full);
  assert.ok(fullText.length > compactText.length * 2);

  const fullAfterCursor = await controller.inspectAgent({
    agent_id: OFFLINE_AGENT_ID,
    cursor: compact.cursor,
    detail: "full",
  });
  assert.equal(fullAfterCursor.state, "idle");
  assert.equal(fullAfterCursor.messages.length, 0);
  assert.equal(fullAfterCursor.final_report, null);
});

test("timeout observing a completed answer while busy does not skip it on the next idle poll", async (t) => {
  let status = () => ({ type: "busy" });
  const messages = () => [
    userMessage("msg_task", "Do the thing"),
    assistantMessage({ id: "msg_answer", text: "the final answer", completed: 5 }),
  ];
  const controller = await offlineController(t, { messages, status: () => status() });

  const busyWait = await controller.waitAgent({
    agent_id: OFFLINE_AGENT_ID,
    cursor: "msg_task",
    timeout_ms: 0,
  });
  assert.equal(busyWait.state, "timed_out");
  assert.equal(busyWait.final_report, null);
  assert.equal(parseCursor(busyWait.cursor).message_id, "msg_task");

  status = () => ({ type: "idle" });
  const done = await controller.waitAgent({
    agent_id: OFFLINE_AGENT_ID,
    cursor: busyWait.cursor,
    timeout_ms: 0,
  });
  assert.equal(done.state, "completed");
  assert.match(done.final_report, /final answer/);

  const replay = await controller.inspectAgent({
    agent_id: OFFLINE_AGENT_ID,
    cursor: done.cursor,
  });
  assert.equal(replay.state, "idle");
  assert.equal(replay.final_report, null);
});

test("fast spawns expose an already-completed answer to the first wait", async (t) => {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "deepseek-fast-spawn-"));
  t.after(async () => {
    await fs.rm(workspace, { recursive: true, force: true });
  });
  const agentID = "ses_fast_spawn";
  const messages = [
    userMessage("msg_task", "Do the thing"),
    assistantMessage({ id: "msg_answer", text: "instant answer", completed: 3 }),
  ];
  const submitted = [];
  const controller = new DeepSeekController({ stateRoot: workspace });
  controller.server = { url: "http://127.0.0.1:4096", authorization: "Basic test", child: null };
  controller.request = async (method, endpoint, options = {}) => {
    if (endpoint === "/provider") return routingCatalog();
    if (endpoint === "/session") return { id: agentID, title: "fast spawn" };
    if (endpoint.endsWith("/prompt_async")) {
      submitted.push(options.body.parts[0].text);
      return {};
    }
    if (endpoint === "/session/status") return { [agentID]: { type: "idle" } };
    if (endpoint.endsWith("/permission") || endpoint.endsWith("/question")) return [];
    if (endpoint.endsWith("/message")) return messages;
    throw new Error(`Unexpected request: ${endpoint}`);
  };

  const started = await controller.spawnAgent({
    task: "Do the thing",
    workspace,
    workspace_mode: "current",
  });
  assert.equal(submitted.length, 1);
  assert.equal(parseCursor(started.cursor).message_id, "msg_task");

  const firstWait = await controller.waitAgent({
    agent_id: agentID,
    cursor: started.cursor,
    timeout_ms: 0,
  });
  assert.equal(firstWait.state, "completed");
  assert.match(firstWait.final_report, /instant answer/);
});

function routingCatalog() {
  return {
    connected: ["opencode-go", "deepseek"],
    all: [
      { id: "opencode-go", models: {
        "deepseek-v4.1-flash": { variants: { low: {}, high: {}, max: {} } },
        "glm-5.3-flash": { variants: { low: {}, high: {}, max: {} } },
      } },
      { id: "deepseek", models: {
        "deepseek-flash": { variants: { low: {}, high: {}, max: {} } },
        "deepseek-v4-pro": { variants: { high: {}, max: {} } },
        "future-official-model": { variants: { max: {} } },
      } },
    ],
  };
}

async function routingController(t, catalog = routingCatalog()) {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "model-routing-"));
  t.after(() => fs.rm(workspace, { recursive: true, force: true }));
  const calls = [];
  const request = async (method, endpoint, options = {}) => {
    calls.push({ method, endpoint, ...options });
    if (endpoint === "/provider") return catalog;
    if (endpoint === "/session") return { id: "ses_route", title: "routing" };
    if (endpoint.endsWith("/fork")) return { id: "ses_child", title: "fork" };
    if (endpoint.endsWith("/prompt_async")) return {};
    if (endpoint === "/session/status") return {};
    if (endpoint.endsWith("/message")) return [];
    if (endpoint.endsWith("/permission") || endpoint.endsWith("/question")) return [];
    throw new Error(`Unexpected routing request: ${endpoint}`);
  };
  const options = { stateRoot: path.join(workspace, "state"), runCommand: async () => ({ code: 0, stdout: "test-version", stderr: "" }) };
  const controller = new DeepSeekController(options);
  controller.request = request;
  return { controller, workspace, calls, request, options };
}

test("all omitted external variants default to max while explicit null stays runtime-default", () => {
  assert.deepEqual(resolveModelSelection(), {provider:"opencode-go", model:"deepseek-v4.1-flash", variant:"max"});
  assert.deepEqual(resolveModelSelection({provider:"deepseek"}), {provider:"deepseek", model:"deepseek-flash", variant:"max"});
  assert.equal(resolveModelSelection({model:"glm-5.3-flash"}).variant, "max");
  assert.equal(resolveModelSelection({provider:"deepseek", model:"deepseek-v4-pro"}).variant, "max");
  assert.equal(resolveModelSelection({variant:null}).variant, null);
  assert.equal(resolveEffectiveSelection({model:"glm-5.3-flash"}).variant, "max");
  assert.equal(resolveEffectiveSelection({provider:"deepseek", model:"deepseek-flash"}).variant, "max");
  assert.equal(resolveEffectiveSelection({provider:"deepseek", model:"deepseek-v4-pro"}).variant, "max");
  assert.equal(
    resolveEffectiveSelection({}, {provider:"opencode-go", model:"glm-5.3-flash", variant:"high"}).variant,
    "high",
  );
  assert.equal(
    resolveEffectiveSelection({}, {provider:"deepseek", model:"deepseek-flash", variant:null}).variant,
    null,
  );
  assert.equal(
    resolveEffectiveSelection({model:"glm-5.3-flash"}, {provider:"opencode-go", model:"deepseek-v4.1-flash", variant:"low"}).variant,
    "max",
  );
  assert.throws(() => resolveModelSelection({provider:"other"}), /provider must/);
  assert.throws(() => resolveModelSelection({model:"deepseek/deepseek-flash"}), /without a provider prefix/);
  assert.throws(() => resolveModelSelection({variant:""}), /variant must/);
});

for (const selection of [
  {},
  {model:"glm-5.3-flash"},
  {model:"glm-5.3-flash",variant:"max"},
  {provider:"deepseek"},
  {provider:"deepseek",model:"deepseek-v4-pro",variant:"high"},
  {provider:"deepseek",model:"future-official-model"},
  {variant:null},
]) {
  test(`spawn uses and persists selected model ${JSON.stringify(selection)}`, async (t) => {
    const {controller,workspace,calls} = await routingController(t);
    const expected = resolveModelSelection(selection);
    const started = await controller.spawnAgent({task:"read code",workspace,workspace_mode:"current",...selection});
    const creation = calls.find(c=>c.endpoint==="/session");
    assert.equal(creation.body.model.providerID, expected.provider);
    assert.equal(creation.body.model.id, expected.model);
    const prompt = calls.find(c=>c.endpoint.endsWith("/prompt_async")).body;
    assert.deepEqual(prompt.model, {providerID:expected.provider,modelID:expected.model});
    assert.equal(prompt.variant, expected.variant ?? undefined);
    if (expected.variant === null) assert.equal(Object.hasOwn(prompt,"variant"),false);
    assert.deepEqual((await controller.readState(started.agent_id)).model_selection,expected);
    assert.equal(started.model,`${expected.provider}/${expected.model}`);
    assert.equal(started.variant,expected.variant);
  });
}

test("preflight separates model catalog, provider configuration and actual authentication", async (t) => {
  const catalog = routingCatalog();
  catalog.connected = ["opencode-go"];
  const {controller,workspace} = await routingController(t,catalog);
  const check = await controller.check({provider:"deepseek",workspace});
  assert.equal(check.model,"deepseek-flash");
  assert.equal(check.model_available,true);
  assert.equal(check.provider_connected,false);
  assert.equal(check.ok,false);
  assert.equal(check.credentials_verified,false);
  assert.equal(check.inference_verified,false);
  catalog.connected.push("deepseek");
  const ready = await controller.check({provider:"deepseek",workspace});
  assert.equal(ready.ok,true);
  assert.equal(ready.credentials_verified,false);
  assert.deepEqual(ready.available_variants,["low","high","max"]);
});

test("invalid routing is rejected before worktree or session creation", async (t) => {
  const catalog = routingCatalog();
  const {controller,workspace,calls} = await routingController(t,catalog);
  controller.prepareDirectory = () => {throw new Error("must not prepare a worktree");};
  for (const [selection,pattern] of [
    [{model:"glm-5.3-flash-other"},/not in the OpenCode catalog/],
    [{model:"glm-5.3-flash",variant:"medium"},/Variant medium is unavailable/],
    [{provider:"deepseek",model:"deepseek-v4-pro",variant:"low"},/Variant low is unavailable/],
    [{model:"toString"},/not in the OpenCode catalog/],
  ]) await assert.rejects(controller.spawnAgent({workspace,task:"test",...selection}),pattern);
  catalog.connected=[];
  await assert.rejects(controller.spawnAgent({workspace,task:"test",provider:"deepseek"}),/Connect deepseek/);
  assert.equal(calls.some(c=>c.endpoint==="/session"),false);
});

test("persisted selection survives restart, immediate and queued followups, forks and inspection", async (t) => {
  const {controller,workspace,calls,request,options} = await routingController(t);
  const selection={provider:"deepseek",model:"deepseek-flash",variant:null};
  await controller.spawnAgent({task:"start",workspace,workspace_mode:"current",...selection});
  const restarted=new DeepSeekController(options);
  restarted.request=request;
  await restarted.sendMessage({agent_id:"ses_route",message:"next"});
  const baseRequest = restarted.request;
  restarted.request = (method,endpoint,options) => endpoint==="/session/status"
    ? {ses_route:{type:"busy"}} : baseRequest(method,endpoint,options);
  const queued=await restarted.sendMessage({agent_id:"ses_route",message:"queued"});
  assert.equal(queued.state,"queued");
  restarted.request=baseRequest;
  await restarted.waitAgent({agent_id:"ses_route",timeout_ms:0});
  assert.equal(await restarted.queueCount("ses_route"),0);
  const fork=await restarted.forkAgent({agent_id:"ses_route"});
  await restarted.sendMessage({agent_id:fork.agent_id,message:"child"});
  const prompts=calls.filter(c=>c.endpoint.endsWith("/prompt_async"));
  assert.equal(prompts.length,4);
  for (const call of prompts) {
    assert.deepEqual(call.body.model,{providerID:"deepseek",modelID:"deepseek-flash"});
    assert.equal(Object.hasOwn(call.body,"variant"),false);
  }
  assert.equal(fork.model,"deepseek/deepseek-flash");
  assert.deepEqual((await restarted.readState(fork.agent_id)).model_selection,selection);
  for (const detail of ["compact","full"]) {
    const status=await restarted.inspectAgent({agent_id:fork.agent_id,detail});
    assert.equal(status.model,"deepseek/deepseek-flash");
    assert.equal(status.variant,null);
  }
  assert.ok((await restarted.listAgents()).agents.every(a=>a.model==="deepseek/deepseek-flash"));
});

test("legacy state uses the original model on followup", async (t) => {
  const {controller,workspace,calls} = await routingController(t);
  await controller.writeState({agent_id:"ses_route",directory:workspace,status:"active"});
  await controller.sendMessage({agent_id:"ses_route",message:"resume"});
  const prompt=calls.find(c=>c.endpoint.endsWith("/prompt_async")).body;
  assert.deepEqual(prompt.model,{providerID:"opencode-go",modelID:"deepseek-v4.1-flash"});
  assert.equal(prompt.variant,"max");
});

const DEFAULTS_SELECTION = {provider:"opencode-go",model:"glm-5.3-flash",variant:"high"};

async function withSavedDefaults(t, selection = DEFAULTS_SELECTION) {
  const routed = await routingController(t);
  await fs.mkdir(routed.controller.stateRoot, { recursive: true, mode: 0o700 });
  if (selection) await routed.controller.writeDefaults(selection);
  return routed;
}

test("model defaults get reports effective selection, source and settings path", async (t) => {
  const {controller} = await routingController(t);
  const builtin = await controller.modelDefaults({action:"get"});
  assert.deepEqual(
    {provider:builtin.provider,model:builtin.model,variant:builtin.variant},
    {provider:"opencode-go",model:"deepseek-v4.1-flash",variant:"max"},
  );
  assert.equal(builtin.configured,"built-in");
  assert.equal(builtin.source,"built_in");
  assert.equal(builtin.settings_path,path.join(controller.stateRoot,"defaults.json"));
  await controller.writeDefaults(DEFAULTS_SELECTION);
  const saved = await controller.modelDefaults({action:"get"});
  assert.deepEqual(
    {provider:saved.provider,model:saved.model,variant:saved.variant},
    DEFAULTS_SELECTION,
  );
  assert.deepEqual(saved.configured,DEFAULTS_SELECTION);
  assert.equal(saved.source,"settings_file");
});

test("model defaults set validates against the catalog and persists atomically with mode 0600", async (t) => {
  const {controller} = await routingController(t);
  const result = await controller.modelDefaults({action:"set",provider:"deepseek",model:"deepseek-flash",variant:"high"});
  assert.deepEqual(
    {provider:result.provider,model:result.model,variant:result.variant},
    {provider:"deepseek",model:"deepseek-flash",variant:"high"},
  );
  assert.deepEqual(result.configured,{provider:"deepseek",model:"deepseek-flash",variant:"high"});
  assert.equal(result.source,"settings_file");
  // Windows uses ACLs; stat.mode does not represent POSIX permission bits.
  if (process.platform !== "win32") {
    const stat = await fs.stat(controller.defaultsPath());
    assert.equal((stat.mode & 0o777),0o600);
  }
  const onDisk = JSON.parse(await fs.readFile(controller.defaultsPath(),"utf8"));
  assert.deepEqual(onDisk,{version:1,provider:"deepseek",model:"deepseek-flash",variant:"high"});
});

test("model defaults set rejects invalid selections and leaves the prior setting unchanged", async (t) => {
  const {controller} = await withSavedDefaults(t);
  const before = await fs.readFile(controller.defaultsPath(),"utf8");
  await assert.rejects(
    controller.modelDefaults({action:"set",model:"missing-model"}),
    /not in the OpenCode catalog/,
  );
  await assert.rejects(
    controller.modelDefaults({action:"set",model:"glm-5.3-flash",variant:"medium"}),
    /Variant medium is unavailable/,
  );
  await assert.rejects(
    controller.modelDefaults({action:"set",provider:"other"}),
    /provider must be/,
  );
  await assert.rejects(controller.modelDefaults({action:"set"}),/set requires at least one/);
  assert.equal(await fs.readFile(controller.defaultsPath(),"utf8"),before);
  assert.deepEqual((await controller.modelDefaults({action:"get"})).variant,"high");
});

test("model defaults reset clears only the settings file even when it is corrupt", async (t) => {
  const {controller} = await withSavedDefaults(t);
  await fs.writeFile(controller.defaultsPath(),"{not json",{mode:0o600});
  await assert.rejects(controller.modelDefaults({action:"get"}),/malformed/);
  const result = await controller.modelDefaults({action:"reset"});
  assert.deepEqual(
    {provider:result.provider,model:result.model,variant:result.variant},
    {provider:"opencode-go",model:"deepseek-v4.1-flash",variant:"max"},
  );
  assert.equal(result.configured,"built-in");
  assert.equal(result.source,"built_in");
  await assert.rejects(fs.access(controller.defaultsPath()),{code:"ENOENT"});
  const after = await controller.modelDefaults({action:"get"});
  assert.equal(after.configured,"built-in");
  assert.equal(after.source,"built_in");
});

test("structurally invalid defaults also error clearly instead of silently falling back", async (t) => {
  const {controller} = await withSavedDefaults(t,null);
  await fs.writeFile(
    controller.defaultsPath(),
    JSON.stringify({version:2,provider:"opencode-go"}),
    {mode:0o600},
  );
  await assert.rejects(controller.modelDefaults({action:"get"}),/unsupported format/);
  await controller.modelDefaults({action:"reset"});
  assert.equal((await controller.modelDefaults({action:"get"})).configured,"built-in");
});

test("a selection object with an invalid model id in the file is also rejected", async (t) => {
  const {controller} = await withSavedDefaults(t,null);
  await fs.writeFile(
    controller.defaultsPath(),
    JSON.stringify({version:1,provider:"opencode-go",model:"a b",variant:null}),
    {mode:0o600},
  );
  await assert.rejects(controller.modelDefaults({action:"get"}),/model must be a model ID/);
});

test("incomplete saved defaults are rejected while explicit null variant remains valid", async (t) => {
  const {controller} = await withSavedDefaults(t,null);
  const valid = {version:1,provider:"opencode-go",model:"glm-5.3-flash",variant:null};
  for (const field of ["provider","model","variant"]) {
    const incomplete = {...valid};
    delete incomplete[field];
    await fs.writeFile(controller.defaultsPath(),JSON.stringify(incomplete));
    await assert.rejects(controller.modelDefaults({action:"get"}), /incomplete.*action=reset/);
    await assert.rejects(controller.check(), /incomplete.*action=reset/);
    await controller.modelDefaults({action:"reset"});
    await assert.rejects(fs.access(controller.defaultsPath()),{code:"ENOENT"});
  }
  await fs.writeFile(controller.defaultsPath(),JSON.stringify(valid));
  const read = await controller.modelDefaults({action:"get"});
  assert.equal(read.variant,null);
  assert.equal(read.model,"glm-5.3-flash");
});

test("explicit options win over saved machine defaults, which win over built-in", async (t) => {
  const {controller} = await routingController(t);
  await controller.writeDefaults({provider:"deepseek",model:"deepseek-v4-pro",variant:"high"});
  assert.deepEqual(
    await controller.modelDefaults({action:"get"}),
    {action:"get",provider:"deepseek",model:"deepseek-v4-pro",variant:"high",configured:{provider:"deepseek",model:"deepseek-v4-pro",variant:"high"},source:"settings_file",settings_path:controller.defaultsPath()},
  );
});

test("partial overrides follow route inheritance rules against saved defaults", () => {
  const saved = {provider:"opencode-go",model:"glm-5.3-flash",variant:"high"};
  assert.deepEqual(resolveEffectiveSelection({},saved),saved);
  assert.deepEqual(resolveEffectiveSelection({variant:"max"},saved),
    {provider:"opencode-go",model:"glm-5.3-flash",variant:"max"});
  assert.deepEqual(resolveEffectiveSelection({variant:null},saved),
    {provider:"opencode-go",model:"glm-5.3-flash",variant:null});
  assert.deepEqual(resolveEffectiveSelection({model:"glm-5.3-flash"},saved),saved);
  assert.deepEqual(resolveEffectiveSelection({model:"deepseek-v4.1-flash"},saved),
    {provider:"opencode-go",model:"deepseek-v4.1-flash",variant:"max"});
  assert.deepEqual(resolveEffectiveSelection({provider:"deepseek"},saved),
    {provider:"deepseek",model:"deepseek-flash",variant:"max"});
  assert.deepEqual(resolveEffectiveSelection({provider:"deepseek",model:"deepseek-flash"},saved),
    {provider:"deepseek",model:"deepseek-flash",variant:"max"});
  assert.deepEqual(resolveEffectiveSelection({provider:"opencode-go",model:"glm-5.3-flash"},saved),
    {provider:"opencode-go",model:"glm-5.3-flash",variant:"high"});
  const noSaved = resolveEffectiveSelection({},null);
  assert.deepEqual(noSaved,{provider:"opencode-go",model:"deepseek-v4.1-flash",variant:"max"});
  assert.deepEqual(resolveEffectiveSelection({variant:"low"},null),
    {provider:"opencode-go",model:"deepseek-v4.1-flash",variant:"low"});
  assert.deepEqual(resolveEffectiveSelection({variant:null},null),
    {provider:"opencode-go",model:"deepseek-v4.1-flash",variant:null});
  assert.deepEqual(resolveEffectiveSelection({model:"deepseek-flash"},null),
    {provider:"opencode-go",model:"deepseek-flash",variant:"max"});
  assert.deepEqual(resolveEffectiveSelection({provider:"deepseek",variant:"high"},null),
    {provider:"deepseek",model:"deepseek-flash",variant:"high"});
  assert.throws(() => resolveEffectiveSelection({provider:"x"},null),/provider must/);
  assert.throws(() => resolveEffectiveSelection({model:"a b"},null),/model must/);
  assert.throws(() => resolveEffectiveSelection({variant:"a b"},null),/variant must/);
});

test("check is read-only: no defaults file is created and an existing one stays byte-identical", async (t) => {
  const {controller,workspace} = await withSavedDefaults(t);
  const before = await fs.readFile(controller.defaultsPath(),"utf8");
  await controller.check({workspace});
  assert.equal(await fs.readFile(controller.defaultsPath(),"utf8"),before);
  const {controller: bare,workspace: bareWorkspace} = await routingController(t);
  await bare.check({workspace: bareWorkspace});
  await assert.rejects(fs.access(bare.defaultsPath()),{code:"ENOENT"});
});

test("new spawns inherit the saved machine default; explicit spawn options still win", async (t) => {
  const {controller,workspace,calls} = await withSavedDefaults(t);
  let sessionCounter = 0;
  const baseRequest = controller.request;
  controller.request = (method, endpoint, options = {}) => {
    if (method === "POST" && endpoint === "/session") {
      calls.push({method, endpoint, ...options});
      return { id: `ses_spawn_${String(++sessionCounter).padStart(2,"0")}`, title: options.body?.title };
    }
    return baseRequest(method, endpoint, options);
  };
  const started = await controller.spawnAgent({task:"inherit",workspace,workspace_mode:"current"});
  const prompt = calls.find(c=>c.endpoint.endsWith("/prompt_async")).body;
  assert.deepEqual(prompt.model,{providerID:"opencode-go",modelID:"glm-5.3-flash"});
  assert.equal(prompt.variant,"high");
  assert.equal(started.model,"opencode-go/glm-5.3-flash");
  const explicit = await controller.spawnAgent({
    task:"explicit",workspace,workspace_mode:"current",provider:"deepseek",model:"deepseek-v4-pro",variant:"high",
  });
  const prompts = calls.filter(c=>c.endpoint.endsWith("/prompt_async"));
  assert.deepEqual(prompts[1].body.model,{providerID:"deepseek",modelID:"deepseek-v4-pro"});
  assert.equal(prompts[1].body.variant,"high");
  assert.deepEqual((await controller.readState(started.agent_id)).model_selection,DEFAULTS_SELECTION);
  assert.deepEqual(
    (await controller.readState(explicit.agent_id)).model_selection,
    {provider:"deepseek",model:"deepseek-v4-pro",variant:"high"},
  );
});

test("existing agents, queued followups, forks and legacy state ignore machine defaults", async (t) => {
  const {controller,workspace,calls,request,options} = await withSavedDefaults(t);
  const saved = {provider:"deepseek",model:"deepseek-flash",variant:null};
  await controller.spawnAgent({task:"start",workspace,workspace_mode:"current",...saved});
  const restarted = new DeepSeekController(options);
  restarted.request = request;
  await restarted.sendMessage({agent_id:"ses_route",message:"next"});
  const baseRequest = restarted.request;
  restarted.request = (method,endpoint,o) => endpoint==="/session/status"
    ? {ses_route:{type:"busy"}} : baseRequest(method,endpoint,o);
  await restarted.sendMessage({agent_id:"ses_route",message:"queued"});
  restarted.request = baseRequest;
  await restarted.waitAgent({agent_id:"ses_route",timeout_ms:0});
  const fork = await restarted.forkAgent({agent_id:"ses_route"});
  await restarted.sendMessage({agent_id:fork.agent_id,message:"child"});
  const prompts = calls.filter(c=>c.endpoint.endsWith("/prompt_async"));
  for (const call of prompts.slice(1)) {
    assert.deepEqual(call.body.model,{providerID:"deepseek",modelID:"deepseek-flash"});
    assert.equal(Object.hasOwn(call.body,"variant"),false);
  }
  assert.equal(prompts.length,4);
  assert.deepEqual((await restarted.readState(fork.agent_id)).model_selection,saved);
  await controller.writeState({agent_id:"ses_legacy",directory:workspace,status:"active"});
  await controller.sendMessage({agent_id:"ses_legacy",message:"legacy"});
  const legacy = calls.filter(c=>c.endpoint.endsWith("/prompt_async")).at(-1).body;
  assert.deepEqual(legacy.model,{providerID:"opencode-go",modelID:"deepseek-v4.1-flash"});
  assert.equal(legacy.variant,"max");
});

test("ds_model_defaults schema exposes actions and omits injected provider default", () => {
  const listed = tools.find(t=>t.name==="ds_model_defaults");
  assert.ok(listed);
  assert.deepEqual(listed.inputSchema.properties.action.enum,["get","set","reset"]);
  assert.ok(listed.inputSchema.properties.provider);
  assert.ok(listed.inputSchema.properties.workspace);
  for (const name of ["ds_check","ds_spawn_agent","ds_model_defaults"]) {
    assert.equal(tools.find(t=>t.name===name).inputSchema.properties.provider.default,undefined);
  }
});


test("wait flushes queued messages only while the raw status is idle, in order", async (t) => {
  let status = () => ({ type: "busy" });
  const messages = [userMessage("msg_task", "start")];
  const submittedTexts = [];
  const controller = await offlineController(t, {
    messages: () => messages,
    status: () => status(),
  });
  const passthrough = controller.request.bind(controller);
  controller.request = async (method, endpoint, options = {}) => {
    if (endpoint.endsWith("/prompt_async")) {
      submittedTexts.push(options.body.parts[0].text);
      messages.push(userMessage(`msg_queued_${submittedTexts.length}`, submittedTexts.at(-1)));
      return {};
    }
    return await passthrough(method, endpoint, options);
  };

  await controller.enqueue(OFFLINE_AGENT_ID, "first follow-up");
  await new Promise((resolve) => setTimeout(resolve, 5));
  await controller.enqueue(OFFLINE_AGENT_ID, "second follow-up");

  const busyWait = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, timeout_ms: 0 });
  assert.equal(busyWait.state, "timed_out");
  assert.deepEqual(submittedTexts, []);
  assert.equal(await controller.queueCount(OFFLINE_AGENT_ID), 2);

  status = () => ({ type: "idle" });
  const idleWait = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, timeout_ms: 0 });
  assert.equal(idleWait.state, "timed_out");
  assert.deepEqual(submittedTexts, ["first follow-up"]);
  assert.equal(await controller.queueCount(OFFLINE_AGENT_ID), 1);
});

test("a completed tool-call step is not a final answer", async (t) => {
  const controller = await offlineController(t, {
    messages: () => [
      userMessage(),
      assistantMessage({
        text: "",
        completed: 4,
        parts: [
          { type: "step-finish", reason: "tool-calls", cost: 0.01, tokens: { input: 1, output: 1 } },
        ],
      }),
    ],
  });

  const wait = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, timeout_ms: 0 });
  assert.equal(wait.state, "timed_out");
  assert.equal(wait.final_report, null);

  const inspected = await controller.inspectAgent({ agent_id: OFFLINE_AGENT_ID });
  assert.equal(inspected.state, "idle");
  assert.equal(inspected.final_report, null);
});

test("partial token fields remain visible even when another message supplies that field", () => {
  const usage = aggregateUsage([
    assistantMessage({ text: "one", completed: 1, cost: 0.1, tokens: { input: 10, output: 2, reasoning: 0, cache: { read: 0, write: 0 }, total: 12 } }),
    assistantMessage({ id: "msg_partial", text: "two", completed: 2, cost: 0.1, tokens: { total: 15 } }),
  ]);
  assert.equal(usage.tokens.total, 27);
  assert.equal(usage.tokens.input, 10);
  assert.equal(usage.token_field_messages.input, 1);
  assert.equal(usage.token_field_messages.total, 2);
  assert.equal(usage.complete, false);
  assert.match(usage.note, /partial/);
});

test("full timeout followed by compact wait cannot consume an undelivered final", async (t) => {
  let busy = true;
  const controller = await offlineController(t, {
    status: () => ({ type: busy ? "busy" : "idle" }),
    messages: () => [userMessage("msg_task", "task"), assistantMessage({ text: "accepted result", completed: 3 })],
  });
  const first = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, cursor: "msg_task", timeout_ms: 0, detail: "full" });
  assert.equal(first.state, "timed_out");
  busy = false;
  const result = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, cursor: first.cursor, timeout_ms: 0 });
  assert.equal(result.state, "completed");
  assert.equal(result.final_report, "accepted result");
});

test("message finish metadata prevents tool steps from becoming final reports", async (t) => {
  const message = assistantMessage({ text: "about to run checks", completed: 4 });
  message.info.finish = "tool-calls";
  const controller = await offlineController(t, { messages: () => [userMessage(), message] });
  const result = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, timeout_ms: 0 });
  assert.equal(result.state, "timed_out");
  assert.equal(result.final_report, null);
});


test("unchanged compact waits omit repeated usage and workspace metadata", async (t) => {
  const controller = await offlineController(t, { messages: () => [userMessage(), assistantMessage({ text: "done", completed: 3 })] });
  const done = await controller.inspectAgent({ agent_id: OFFLINE_AGENT_ID });
  assert.ok(done.usage);
  assert.ok(done.worktree);
  const waiting = await controller.waitAgent({ agent_id: OFFLINE_AGENT_ID, cursor: done.cursor, timeout_ms: 0 });
  assert.equal(waiting.state, "timed_out");
  assert.ok(!("usage" in waiting));
  assert.ok(!("worktree" in waiting));
  assert.deepEqual(waiting.pending, { permissions: [], questions: [] });
  assert.ok(JSON.stringify(waiting).length < 400);
});

const pluginRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

test("model-defaults CLI parses actions and flags using only a temporary state root", async (t) => {
  const stateRoot = await fs.mkdtemp(path.join(os.tmpdir(), "model-defaults-cli-"));
  t.after(() => fs.rm(stateRoot, { recursive: true, force: true }));
  const run = (args) =>
    new Promise((resolve) => {
      const child = spawn(
        process.execPath,
        [path.join(pluginRoot, "scripts", "model-defaults.mjs"), ...args],
        {
          env: { ...process.env, DEEPSEEK_AGENT_STATE_DIR: stateRoot },
          stdio: ["ignore", "pipe", "pipe"],
          windowsHide: true,
        },
      );
      let stdout = "";
      let stderr = "";
      child.stdout.on("data", (chunk) => { stdout += chunk; });
      child.stderr.on("data", (chunk) => { stderr += chunk; });
      child.on("close", (code) => resolve({ code, stdout, stderr }));
    });

  const missing = await run(["bogus"]);
  assert.equal(missing.code, 1);
  assert.match(missing.stderr, /Usage: model-defaults\.mjs get\|set\|reset/);

  const noSet = await run(["set"]);
  assert.equal(noSet.code, 1);
  assert.match(noSet.stderr, /set requires at least one/);

  const badFlag = await run(["set", "--unknown", "x"]);
  assert.equal(badFlag.code, 1);
  assert.match(badFlag.stderr, /Usage: model-defaults\.mjs/);

  const danglingFlag = await run(["set", "--variant"]);
  assert.equal(danglingFlag.code, 1);
  assert.match(danglingFlag.stderr, /Usage: model-defaults\.mjs/);

  const getWithOptions = await run(["get", "--provider", "deepseek"]);
  assert.equal(getWithOptions.code, 1);
  assert.match(getWithOptions.stderr, /get takes no options/);

  const initial = await run(["get"]);
  assert.equal(initial.code, 0);
  const initialPayload = JSON.parse(initial.stdout);
  assert.equal(initialPayload.action, "get");
  assert.equal(initialPayload.model, "deepseek-v4.1-flash");
  assert.equal(initialPayload.variant, "max");
  assert.equal(initialPayload.source, "built_in");
  assert.ok(initialPayload.settings_path.startsWith(stateRoot));
  await assert.rejects(fs.access(path.join(stateRoot, "defaults.json")), { code: "ENOENT" });

  await fs.writeFile(
    path.join(stateRoot, "defaults.json"),
    "{corrupt",
    { mode: 0o600 },
  );
  const corrupt = await run(["get"]);
  assert.equal(corrupt.code, 1);
  assert.match(corrupt.stderr, /malformed/);

  const reset = await run(["reset"]);
  assert.equal(reset.code, 0);
  const resetPayload = JSON.parse(reset.stdout);
  assert.equal(resetPayload.action, "reset");
  assert.equal(resetPayload.configured, "built-in");
  assert.equal(resetPayload.source, "built_in");
  await assert.rejects(fs.access(path.join(stateRoot, "defaults.json")), { code: "ENOENT" });
  const restored = await run(["get"]);
  assert.equal(restored.code, 0);
  assert.equal(JSON.parse(restored.stdout).source, "built_in");
});
