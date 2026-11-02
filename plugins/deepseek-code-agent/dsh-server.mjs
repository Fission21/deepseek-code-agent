#!/usr/bin/env node
/**
 * MCP launcher for the bundled DSH controller.
 *
 * Codex starts MCP servers with Node, so this file resolves a usable Python
 * interpreter and the skill-relative server script. It does not require Python
 * to be called `python3` on every platform.
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { pythonErrorMessage, resolvePython } from "./scripts/python.mjs";

const root = path.dirname(fileURLToPath(import.meta.url));
const scripts = path.join(root, "skills", "dsh-delegate", "scripts");
const args = process.argv.slice(2);
const checkMode = args.includes("--check");
const passArgs = args.filter((arg) => arg !== "--check");
const script = path.join(scripts, checkMode ? "preflight.py" : "mcp_server.py");

if (!existsSync(script)) {
  process.stderr.write(`DSH server script is missing: ${script}\n`);
  process.exit(1);
}

const selected = resolvePython();
if (!selected) {
  process.stderr.write(`${pythonErrorMessage()}\n`);
  process.exit(1);
}

const child = spawn(
  selected.command,
  [...selected.prefix, script, ...passArgs],
  { stdio: "inherit", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" }, windowsHide: true },
);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    try { child.kill(signal); } catch { /* already gone */ }
  });
}

child.on("error", (error) => {
  process.stderr.write(`Unable to start the DSH controller: ${error.message}\n`);
  process.exit(1);
});
child.on("exit", (code, signal) => {
  process.exit(code ?? (signal ? 1 : 0));
});
