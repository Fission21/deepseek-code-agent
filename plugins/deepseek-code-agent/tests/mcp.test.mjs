import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import readline from "node:readline";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const stateRoot = await fs.mkdtemp(path.join(os.tmpdir(), "deepseek-mcp-state-"));
let child;
let lines;
let nextID = 1;
const pending = new Map();

function request(method, params = {}) {
  const id = nextID++;
  child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`timeout waiting for ${method}`));
    }, 20_000);
    pending.set(id, { resolve, reject, timer });
  });
}

before(async () => {
  child = spawn(process.execPath, [path.join(root, "server.mjs")], {
    cwd: root,
    env: { ...process.env, DEEPSEEK_AGENT_STATE_DIR: stateRoot },
    stdio: ["pipe", "pipe", "pipe"],
  });
  lines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  lines.on("line", (line) => {
    const message = JSON.parse(line);
    const waiter = pending.get(message.id);
    if (!waiter) return;
    clearTimeout(waiter.timer);
    pending.delete(message.id);
    if (message.error) waiter.reject(new Error(message.error.message));
    else waiter.resolve(message.result);
  });
  await request("initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "test", version: "1" },
  });
  child.stdin.write(
    `${JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" })}\n`,
  );
});

after(() => {
  lines?.close();
  child?.kill("SIGTERM");
  void fs.rm(stateRoot, { recursive: true, force: true });
});

test("MCP initializes and keeps native Luna outside the external provider schema", async () => {
  const result = await request("tools/list");
  assert.ok(result.tools.length >= 10);
  assert.ok(result.tools.some((tool) => tool.name === "ds_spawn_agent"));
  for (const name of ["ds_check", "ds_spawn_agent", "ds_model_defaults"]) {
    const schema = result.tools.find((tool) => tool.name === name).inputSchema;
    assert.deepEqual(schema.properties.provider.enum, ["opencode-go", "deepseek"]);
    assert.deepEqual(schema.properties.variant.type, ["string", "null"]);
    assert.ok(schema.properties.model);
  }
});

test("MCP check forwards invalid provider selection instead of silently using the default", async () => {
  const result = await request("tools/call", { name: "ds_check", arguments: {provider: "unsupported"} });
  assert.equal(result.isError, true);
  assert.match(result.content[0].text, /provider must be/);
});

test("MCP prerequisite check sees the configured model", {
  skip:
    process.env.DEEPSEEK_LIVE_PREREQ !== "1"
      ? "Set DEEPSEEK_LIVE_PREREQ=1 on a configured machine"
      : false,
}, async () => {
  const result = await request("tools/call", { name: "ds_check", arguments: {} });
  assert.equal(result.isError, undefined);
  const payload = JSON.parse(result.content[0].text);
  assert.equal(payload.model, "deepseek-v4.1-flash");
  assert.equal(payload.variant, "max");
  assert.equal(payload.ok, true);
});

test("MCP forwards ds_model_defaults get/reset and rejects invalid actions", async () => {
  const listed = await request("tools/list");
  const schema = listed.tools.find((tool) => tool.name === "ds_model_defaults").inputSchema;
  assert.deepEqual(schema.properties.action.enum, ["get", "set", "reset"]);

  const invalid = await request("tools/call", {
    name: "ds_model_defaults",
    arguments: { action: "bogus" },
  });
  assert.equal(invalid.isError, true);
  assert.match(invalid.content[0].text, /action must be get, set, or reset/);

  const emptySet = await request("tools/call", {
    name: "ds_model_defaults",
    arguments: { action: "set" },
  });
  assert.equal(emptySet.isError, true);
  assert.match(emptySet.content[0].text, /set requires at least one/);

  const initial = await request("tools/call", {
    name: "ds_model_defaults",
    arguments: {},
  });
  assert.equal(initial.isError, undefined);
  const initialPayload = JSON.parse(initial.content[0].text);
  assert.equal(initialPayload.action, "get");
  assert.equal(initialPayload.provider, "opencode-go");
  assert.equal(initialPayload.model, "deepseek-v4.1-flash");
  assert.equal(initialPayload.variant, "max");
  assert.equal(initialPayload.configured, "built-in");
  assert.equal(initialPayload.source, "built_in");
  assert.ok(initialPayload.settings_path.endsWith("defaults.json"));

  const reset = await request("tools/call", {
    name: "ds_model_defaults",
    arguments: { action: "reset" },
  });
  assert.equal(reset.isError, undefined);
  const resetPayload = JSON.parse(reset.content[0].text);
  assert.equal(resetPayload.action, "reset");
  assert.equal(resetPayload.configured, "built-in");
  assert.equal(resetPayload.source, "built_in");
  await assert.rejects(fs.access(path.join(stateRoot, "defaults.json")), { code: "ENOENT" });
});
