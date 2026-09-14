import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import readline from "node:readline";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { after, before, test } from "node:test";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
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
});

test("MCP initializes and lists tools", async () => {
  const result = await request("tools/list");
  assert.ok(result.tools.length >= 10);
  assert.ok(result.tools.some((tool) => tool.name === "ds_spawn_agent"));
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
