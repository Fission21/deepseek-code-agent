import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  DeepSeekController,
  buildInitialTask,
  normalizeTaskSpec,
  renderTaskSpecSection,
} from "../src/controller.mjs";
import { tools } from "../server.mjs";

const AGENT_ID = "ses_workflow";

function userMessage(id = "msg_user", text = "Please implement the change.") {
  return {
    info: { id, role: "user", time: { created: 1 } },
    parts: [{ type: "text", text }],
  };
}

function assistantMessage({ id = "msg_final", text = "done", completed = 2 } = {}) {
  return {
    info: { id, role: "assistant", time: { created: 1, completed } },
    parts: [{ type: "text", text }],
  };
}

const BASE_SPEC = {
  version: 1,
  design_decisions: ["Use a single pass; no extra deps."],
  acceptance_criteria: ["npm test passes locally."],
  checks: [
    { id: "unit", argv: ["node", "--test"], timeout_ms: 30000 },
    { id: "lint", argv: ["node", "lint.mjs"] },
  ],
};

test("normalizeTaskSpec accepts a valid spec and applies defaults", () => {
  const spec = normalizeTaskSpec(JSON.parse(JSON.stringify(BASE_SPEC)));
  assert.equal(spec.version, 1);
  assert.deepEqual(spec.design_decisions, ["Use a single pass; no extra deps."]);
  assert.deepEqual(spec.checks[0], {
    id: "unit",
    argv: ["node", "--test"],
    cwd: ".",
    timeout_ms: 30000,
  });
  assert.deepEqual(spec.checks[1], {
    id: "lint",
    argv: ["node", "lint.mjs"],
    cwd: ".",
    timeout_ms: 60000,
  });
});

test("normalizeTaskSpec normalizes cwd separators and nested relative paths", () => {
  const spec = normalizeTaskSpec({
    version: 1,
    checks: [{ id: "a", argv: ["x"], cwd: "sub\\dir/./here" }],
  });
  assert.equal(spec.checks[0].cwd, "sub/dir/here");
});

test("normalizeTaskSpec returns null when omitted", () => {
  assert.equal(normalizeTaskSpec(undefined), null);
  assert.equal(normalizeTaskSpec(null), null);
});

test("normalizeTaskSpec rejects malformed specs", () => {
  const cases = [
    [{}, /version must be 1/],
    [{ version: 2 }, /version must be 1/],
    ["nope", /must be an object/],
    [{ version: 1, extra: true }, /unknown fields: extra/],
    [{ version: 1, design_decisions: [""] }, /non-empty strings/],
    [{ version: 1, design_decisions: ["x".repeat(2001)] }, /at most 2000/],
    [{ version: 1, design_decisions: "nope" }, /must be an array/],
    [{ version: 1, checks: [{ id: "a", argv: [] }] }, /non-empty array/],
    [{ version: 1, checks: [{ id: "a", argv: [""] }] }, /non-empty array|non-empty strings/],
    [{ version: 1, checks: [{ id: "a", argv: ["bad\0"] }] }, /NUL/],
    [{ version: 1, checks: [{ id: "a", argv: ["x"], cwd: "C:/abs" }] }, /workspace-relative/],
    [{ version: 1, checks: [{ id: "a", argv: ["x"], cwd: "/abs" }] }, /workspace-relative/],
    [{ version: 1, checks: [{ id: "a", argv: ["x"], cwd: "../out" }] }, /inside the workspace/],
    [{ version: 1, checks: [{ id: "a", argv: ["x"], cwd: "bad\0cwd" }] }, /NUL/],
    [{ version: 1, checks: [{ id: "a", argv: ["x"], cwd: "" }] }, /non-empty relative path/],
    [{ version: 1, checks: [{ id: "", argv: ["x"] }] }, /non-empty strings/],
    [{ version: 1, checks: [{ id: "a".repeat(101), argv: ["x"] }] }, /at most 100/],
    [
      { version: 1, checks: [{ id: "a", argv: ["x"] }, { id: "a", argv: ["y"] }] },
      /must be unique: a/,
    ],
    [
      { version: 1, checks: [{ id: "a", argv: ["x"] }, { id: "a", argv: ["y"] }] },
      /must be unique/,
    ],
    [{ version: 1, checks: [{ id: "a", argv: ["x"], timeout_ms: 0 }] }, /between 1 and/],
    [{ version: 1, checks: [{ id: "a", argv: ["x"], timeout_ms: 300001 }] }, /between 1 and/],
    [{ version: 1, checks: [{ id: "a", argv: ["x"], timeout_ms: 1.5 }] }, /between 1 and/],
    [{ version: 1, checks: [{ id: "a", argv: ["x"], unknown: 1 }] }, /unknown fields: unknown/],
    [
      {
        version: 1,
        checks: Array.from({ length: 21 }, (_, i) => ({ id: `c${i}`, argv: ["x"] })),
      },
      /at most 20 checks/,
    ],
  ];
  for (const [input, pattern] of cases) {
    assert.throws(() => normalizeTaskSpec(input), pattern, JSON.stringify(input));
  }
});

test("spawnAgent without task_spec keeps legacy state and prompt", async (t) => {
  const { controller, submitted, calls } = await spawnController(t);
  const started = await controller.spawnAgent({
    task: "read code",
    workspace: t.workspace,
    workspace_mode: "current",
  });
  const state = await controller.readState(started.agent_id);
  assert.equal(state.task_spec, null);
  assert.doesNotMatch(submitted[0], /Task specification/);
  assert.ok(calls.some((call) => call.endpoint === "/session"));
});

test("spawnAgent persists the normalized spec and renders it into the initial task", async (t) => {
  const { controller, submitted } = await spawnController(t);
  const started = await controller.spawnAgent({
    task: "read code",
    workspace: t.workspace,
    workspace_mode: "current",
    task_spec: BASE_SPEC,
  });
  const state = await controller.readState(started.agent_id);
  assert.deepEqual(state.task_spec, normalizeTaskSpec(BASE_SPEC));
  assert.match(submitted[0], /Task specification \(v1\)/);
  assert.match(submitted[0], /Use a single pass; no extra deps\./);
  assert.match(submitted[0], /npm test passes locally\./);
  assert.match(submitted[0], /unit: cwd=\.\s+timeout_ms=30000 argv=\["node","--test"\]/);
  assert.match(submitted[0], /lint: cwd=\.\s+timeout_ms=60000/);
  assert.match(submitted[0], /escalate only an actual conflict/);
  assert.match(started.task_spec.checks[0].id, /^unit$/);
});

test("spawnAgent rejects an invalid spec before any session or worktree is created", async (t) => {
  const { controller, calls } = await spawnController(t);
  await assert.rejects(
    controller.spawnAgent({
      task: "bad spec",
      workspace: t.workspace,
      workspace_mode: "current",
      task_spec: { version: 1, checks: [{ id: "dup", argv: [] }] },
    }),
    /argv/,
  );
  assert.equal(calls.some((call) => call.endpoint === "/session"), false);
  assert.equal(calls.some((call) => call.endpoint.endsWith("/prompt_async")), false);
});

test("spawnAgent rejects a check cwd outside the prepared workspace", async (t) => {
  const { controller, calls } = await spawnController(t);
  await assert.rejects(
    controller.spawnAgent({
      task: "escape",
      workspace: t.workspace,
      workspace_mode: "current",
      task_spec: { version: 1, checks: [{ id: "a", argv: ["x"], cwd: "../outside" }] },
    }),
    /inside the workspace/,
  );
  assert.equal(calls.some((call) => call.endpoint === "/session"), false);
});

test("renderTaskSpecSection is empty without a spec and bounded with one", () => {
  assert.equal(renderTaskSpecSection(null), "");
  const section = renderTaskSpecSection(normalizeTaskSpec(BASE_SPEC));
  assert.ok(section.length < 2000);
  assert.doesNotMatch(section, /Design decisions.*Design decisions/s);
});

test("MCP schemas expose task_spec and return_on with the exact v1 bounds", () => {
  const spawn = tools.find((tool) => tool.name === "ds_spawn_agent");
  const taskSpec = spawn.inputSchema.properties.task_spec;
  assert.ok(taskSpec);
  assert.deepEqual(taskSpec.properties.checks.maxItems, 20);
  assert.deepEqual(
    taskSpec.properties.checks.items.properties.timeout_ms.maximum,
    300000,
  );
  assert.deepEqual(taskSpec.required, ["version"]);
  const wait = tools.find((tool) => tool.name === "ds_wait_agent");
  assert.deepEqual(wait.inputSchema.properties.return_on.enum, ["legacy", "actionable"]);
  assert.equal(wait.inputSchema.properties.return_on.default, "legacy");
});

async function spawnController(t) {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "ds-spec-workspace-"));
  t.workspace = workspace;
  t.after(async () => {
    await fs.rm(workspace, { recursive: true, force: true });
  });
  const calls = [];
  const submitted = [];
  const controller = new DeepSeekController({
    stateRoot: path.join(workspace, "state"),
    runCommand: async () => ({ code: 0, stdout: "test-version", stderr: "" }),
  });
  controller.request = async (method, endpoint, options = {}) => {
    calls.push({ method, endpoint });
    if (endpoint === "/provider") {
      return {
        connected: ["opencode-go"],
        all: [
          {
            id: "opencode-go",
            models: {
              "deepseek-v4.1-flash": { variants: { low: {}, high: {}, max: {} } },
            },
          },
        ],
      };
    }
    if (endpoint === "/session") return { id: AGENT_ID, title: "spec spawn" };
    if (endpoint.endsWith("/prompt_async")) {
      submitted.push(options.body.parts[0].text);
      return {};
    }
    if (endpoint === "/session/status") return {};
    if (endpoint.endsWith("/message")) return [userMessage("msg_task")];
    if (endpoint.endsWith("/permission") || endpoint.endsWith("/question")) return [];
    throw new Error(`Unexpected request: ${method} ${endpoint}`);
  };
  return { controller, workspace, calls, submitted };
}

async function waitController(t, { clock } = {}) {
  const stateRoot = await fs.mkdtemp(path.join(os.tmpdir(), "ds-wait-state-"));
  t.after(async () => {
    await fs.rm(stateRoot, { recursive: true, force: true });
  });
  let statusType = "idle";
  const messages = [userMessage("msg_task", "do the thing")];
  const submitted = [];
  const controller = new DeepSeekController({
    stateRoot,
    spawn: () => {
      throw new Error("spawn must not be called in offline tests");
    },
    ...(clock ? { now: () => clock.value } : {}),
  });
  controller.server = { url: "http://127.0.0.1:4096", authorization: "Basic test", child: null };
  controller.request = async (method, endpoint, options = {}) => {
    if (endpoint === "/session/status") {
      return { [AGENT_ID]: { type: statusType } };
    }
    if (endpoint.endsWith("/permission") || endpoint.endsWith("/question")) {
      return [];
    }
    if (endpoint.endsWith("/message")) return messages;
    if (endpoint.endsWith("/prompt_async")) {
      submitted.push(options.body?.parts?.[0]?.text ?? "");
      return {};
    }
    if (endpoint.endsWith("/abort")) return {};
    throw new Error(`Unexpected offline request: ${method} ${endpoint}`);
  };
  await controller.writeState({
    agent_id: AGENT_ID,
    status: "active",
    directory: stateRoot,
    source_root: stateRoot,
    workspace_mode: "current",
    temp_root: null,
    owns_worktree: false,
    created_at: 1,
    title: "wait fixture",
    scope_paths: [],
    instruction_manifest: { files: [], total_bytes: 0 },
  });
  return {
    controller,
    agentID: AGENT_ID,
    messages,
    submitted,
    setStatus: (value) => {
      statusType = value;
    },
  };
}

test("actionable mode consumes a transient retry and reports completion on recovery", async (t) => {
  const clock = { value: 1_000_000 };
  const { controller, agentID, messages, setStatus } = await waitController(t, { clock });
  setStatus("retry");
  const transient = await controller.waitAgent({
    agent_id: agentID,
    timeout_ms: 0,
    return_on: "actionable",
  });
  assert.equal(transient.state, "timed_out");

  setStatus("idle");
  messages.push(assistantMessage({ id: "msg_done", text: "all green", completed: 3 }));
  const done = await controller.waitAgent({
    agent_id: agentID,
    cursor: transient.cursor,
    timeout_ms: 0,
    return_on: "actionable",
  });
  assert.equal(done.state, "completed");
  assert.equal(done.final_report, "all green");
});

test("actionable escalation keeps a continuous retry streak across calls", async (t) => {
  const clock = { value: 10_000 };
  const { controller, agentID, setStatus } = await waitController(t, { clock });

  setStatus("retry");
  const first = await controller.waitAgent({
    agent_id: agentID,
    timeout_ms: 0,
    return_on: "actionable",
  });
  assert.equal(first.state, "timed_out");

  clock.value = 10_000 + 110_000;
  const underBudget = await controller.waitAgent({
    agent_id: agentID,
    timeout_ms: 0,
    return_on: "actionable",
  });
  assert.equal(underBudget.state, "timed_out");

  clock.value = 10_000 + 130_000;
  const escalated = await controller.waitAgent({
    agent_id: agentID,
    timeout_ms: 0,
    return_on: "actionable",
  });
  assert.equal(escalated.state, "needs_attention");
  assert.equal(escalated.reason, "provider_retry_budget");
  assert.ok(escalated.retry.retry_streak_ms >= 120_000);
  assert.ok(escalated.cursor);
});

test("actionable escalation carries real retry details", async (t) => {
  const clock = { value: 500_000 };
  const { controller, agentID, setStatus } = await waitController(t, { clock });
  controller.rawStatus = async () => ({
    type: "retry",
    message: "provider overloaded",
    attempts: 7,
  });
  await controller.waitAgent({ agent_id: agentID, timeout_ms: 0, return_on: "actionable" });
  clock.value += 121_000;
  const escalated = await controller.waitAgent({
    agent_id: agentID,
    timeout_ms: 0,
    return_on: "actionable",
  });
  assert.equal(escalated.retry.message, "provider overloaded");
  assert.equal(escalated.retry.attempts, 7);
});

test("actual recovery resets the retry streak instead of escalating stale time", async (t) => {
  const clock = { value: 1_000 };
  const { controller, agentID, setStatus } = await waitController(t, { clock });

  setStatus("retry");
  await controller.waitAgent({ agent_id: agentID, timeout_ms: 0, return_on: "actionable" });

  clock.value = 200_000;
  setStatus("idle");
  const recovered = await controller.waitAgent({
    agent_id: agentID,
    timeout_ms: 0,
    return_on: "actionable",
  });
  assert.equal(recovered.state, "timed_out");

  setStatus("retry");
  const freshStreak = await controller.waitAgent({
    agent_id: agentID,
    timeout_ms: 0,
    return_on: "actionable",
  });
  assert.equal(freshStreak.state, "timed_out");
  assert.notEqual(freshStreak.state, "needs_attention");
});

test("actionable ordinary timeout returns only agent_id, state, and cursor", async (t) => {
  const clock = { value: 5_000 };
  const { controller, agentID } = await waitController(t, { clock });
  controller.rawStatus = async () => ({ type: "busy" });
  const timedOut = await controller.waitAgent({
    agent_id: agentID,
    timeout_ms: 0,
    return_on: "actionable",
  });
  assert.deepEqual(Object.keys(timedOut).sort(), ["agent_id", "cursor", "state"]);
  assert.equal(timedOut.state, "timed_out");
});

test("actionable keeps immediate precedence for completion, failure and pending requests", async (t) => {
  const clock = { value: 1_000 };
  const { controller, agentID, messages, setStatus } = await waitController(t, { clock });

  setStatus("retry");
  await controller.waitAgent({ agent_id: agentID, timeout_ms: 0, return_on: "actionable" });
  clock.value += 200_000;

  setStatus("idle");
  messages.push(assistantMessage({ id: "msg_done", text: "finished early", completed: 3 }));
  const completed = await controller.waitAgent({
    agent_id: agentID,
    timeout_ms: 0,
    return_on: "actionable",
  });
  assert.equal(completed.state, "completed");
  assert.equal(completed.final_report, "finished early");
  assert.ok(!controller.retryStreaks.has(agentID));
});

test("legacy wait still returns retry immediately and full timeout payloads", async (t) => {
  const { controller, agentID, setStatus } = await waitController(t);
  setStatus("retry");
  const retry = await controller.waitAgent({ agent_id: agentID, timeout_ms: 0 });
  assert.equal(retry.state, "retry");
  assert.equal(retry.detail, "compact");

  setStatus("idle");
  const legacy = await controller.waitAgent({
    agent_id: agentID,
    timeout_ms: 0,
    return_on: "legacy",
  });
  assert.equal(legacy.state, "timed_out");
  assert.ok("final_report" in legacy);
  assert.ok("queued_messages" in legacy);
});

test("return_on rejects unknown modes", async (t) => {
  const { controller, agentID } = await waitController(t);
  await assert.rejects(
    controller.waitAgent({ agent_id: agentID, timeout_ms: 0, return_on: "soon" }),
    /return_on/,
  );
});

test("concurrent queue flushes submit exactly once", async (t) => {
  const clock = { value: 1_000 };
  const { controller, agentID, submitted } = await waitController(t, { clock });
  await controller.enqueue(agentID, "follow-up");
  await new Promise((resolve) => setTimeout(resolve, 5));
  const state = await controller.readState(agentID);
  const generation = controller.currentGeneration(agentID);
  const slowSubmit = controller.submit.bind(controller);
  controller.submit = async (...args) => {
    await new Promise((resolve) => setTimeout(resolve, 25));
    return slowSubmit(...args);
  };
  const [first, second] = await Promise.all([
    controller.maybeFlushQueued(state, generation),
    controller.maybeFlushQueued(state, generation),
  ]);
  assert.deepEqual([first, second].filter(Boolean).length, 1);
  assert.deepEqual(submitted, ["follow-up"]);
  assert.equal(await controller.queueCount(agentID), 0);
});

test("an interrupted waiter never flushes a queued message after stop", async (t) => {
  const { controller, agentID, submitted } = await waitController(t);
  await controller.enqueue(agentID, "late flush attempt");
  await new Promise((resolve) => setTimeout(resolve, 5));
  let releasePending;
  const gate = new Promise((resolve) => {
    releasePending = resolve;
  });
  const originalPending = controller.pending.bind(controller);
  controller.pending = async (state) => {
    await gate;
    return await originalPending(state);
  };
  const generationBefore = controller.currentGeneration(agentID);
  const waiter = controller.waitAgent({ agent_id: agentID, timeout_ms: 30_000 });
  await new Promise((resolve) => setTimeout(resolve, 30));
  await controller.interruptAgent({ agent_id: agentID });
  releasePending();
  const result = await waiter;
  assert.equal(controller.currentGeneration(agentID), generationBefore + 1);
  assert.deepEqual(submitted, []);
  assert.equal(await controller.queueCount(agentID), 1);
  assert.ok(result);

  controller.pending = originalPending;
  const followUp = await controller.waitAgent({ agent_id: agentID, timeout_ms: 0 });
  assert.deepEqual(submitted, ["late flush attempt"]);
  assert.equal(await controller.queueCount(agentID), 0);
  assert.equal(followUp.state, "timed_out");
});
