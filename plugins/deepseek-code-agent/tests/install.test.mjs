import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { readFileSync, mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import readline from "node:readline";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const plugin = JSON.parse(readFileSync(path.join(root, ".codex-plugin/plugin.json"), "utf8"));
const pkg = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
const mcp = JSON.parse(readFileSync(path.join(root, ".mcp.json"), "utf8"));

test("plugin defaults to DSH and keeps OpenCode as an explicit legacy server", () => {
  assert.equal(plugin.version, "0.2.0");
  assert.equal(pkg.version, "0.2.0");
  assert.match(plugin.description, /DeepSeek Harness \(DSH\) by default/);
  assert.deepEqual(Object.keys(mcp.mcpServers), ["dsh_agents", "deepseek_code_agent"]);
  assert.equal(mcp.mcpServers.dsh_agents.args[0], "./dsh-server.mjs");
  assert.equal(mcp.mcpServers.deepseek_code_agent.args[0], "./server.mjs");
  assert.ok(mcp.mcpServers.dsh_agents.env_vars.includes("DSH_BIN"));
  assert.ok(mcp.mcpServers.dsh_agents.env_vars.includes("DSH_PYTHON"));
  assert.equal(pkg.scripts.check, "node ./dsh-server.mjs --check");
  assert.equal(pkg.scripts["check:legacy"], "node ./scripts/check-prereqs.mjs");
  assert.equal(pkg.scripts["test:legacy-live"], "node ./tests/live-smoke.mjs");
  assert.equal(pkg.scripts["test:live"], undefined);
  assert.match(pkg.scripts.test, /test-all\.mjs/);
});

test("the DSH skill is implicit, while the legacy skill cannot steal routing", () => {
  assert.ok(existsSync(path.join(root, "skills/dsh-delegate/SKILL.md")));
  assert.ok(existsSync(path.join(root, "skills/dsh-delegate/scripts/mcp_server.py")));
  assert.ok(existsSync(path.join(root, "skills/dsh-delegate/runtime/controller_link.mjs")));
  const legacy = readFileSync(path.join(root, "skills/deepseek-coding-delegation/agents/openai.yaml"), "utf8");
  assert.match(legacy, /allow_implicit_invocation:\s*false/);
  const dsh = readFileSync(path.join(root, "skills/dsh-delegate/agents/openai.yaml"), "utf8");
  assert.doesNotMatch(dsh, /allow_implicit_invocation:\s*false/);
  const legacySkill = readFileSync(path.join(root, "skills/deepseek-coding-delegation/SKILL.md"), "utf8");
  assert.match(legacySkill, /Legacy, explicit-only lane/);
  assert.match(legacySkill, /\$dsh-delegate/);
});

test("manifest prompts stay within the Codex limits", () => {
  assert.ok(Array.isArray(plugin.interface.defaultPrompt));
  assert.ok(plugin.interface.defaultPrompt.length <= 3);
  for (const prompt of plugin.interface.defaultPrompt) {
    assert.ok(prompt.length <= 128, prompt);
    assert.ok(prompt.trim().length > 0);
  }
});

test("the default dsh_agents MCP launcher answers initialize and tools/list", async () => {
  const state = mkdtempSync(path.join(tmpdir(), "dsh-launcher-"));
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
  delete env.DSH_PYTHON;
  const child = spawn(process.execPath, [path.join(root, "dsh-server.mjs"), "--root", state], {
    cwd: root,
    env,
    stdio: ["pipe", "pipe", "pipe"],
  });
  const lines = [];
  const reader = readline.createInterface({ input: child.stdout });
  reader.on("line", (line) => lines.push(JSON.parse(line)));
  const waitFor = async (predicate, timeout = 10000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const found = lines.find(predicate);
      if (found) return found;
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    throw new Error("timed out waiting for MCP response");
  };
  try {
    child.stdin.write(`${JSON.stringify({
      jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18" },
    })}\n`);
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list" })}\n`);
    const initialized = await waitFor((line) => line.id === 1);
    assert.equal(initialized.result.serverInfo.name, "dsh-agents");
    const listed = await waitFor((line) => line.id === 2);
    assert.equal(listed.result.tools.length, 8);
  } finally {
    reader.close();
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGTERM");
      await new Promise((resolve) => child.once("close", resolve));
    }
    rmSync(state, { recursive: true, force: true });
  }
});

test("an invalid DSH_PYTHON is reported instead of silently falling back", () => {
  const result = spawnSync(process.execPath, [path.join(root, "dsh-server.mjs"), "--check"], {
    cwd: root,
    env: { ...process.env, DSH_PYTHON: path.join(root, "missing-python") },
    encoding: "utf8",
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /DSH_PYTHON/);
  assert.match(result.stderr, /no PATH fallback/);
});

test("READMEs document the DSH-first flow and keep historical evidence separate", () => {
  for (const file of ["../../README.md", "../../README.zh-CN.md"]) {
    const text = readFileSync(path.join(root, file), "utf8");
    assert.match(text, /```mermaid/);
    assert.match(text, /spawn_agent/);
    assert.match(text, /send_message/);
    assert.match(text, /reply_agent/);
    assert.match(text, /close_agent/);
    assert.match(text, /DeepSeek Harness|DSH/);
    assert.match(text, /not a DSH benchmark|不是 DSH benchmark/);
    assert.match(text, /Windows/);
  }
  const pluginReadme = readFileSync(path.join(root, "README.md"), "utf8");
  assert.match(pluginReadme, /dsh_agents/);
  assert.match(pluginReadme, /test:legacy-live/);
});
