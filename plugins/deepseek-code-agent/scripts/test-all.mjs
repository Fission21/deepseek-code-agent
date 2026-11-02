import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { pythonErrorMessage, resolvePython } from "./python.mjs";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const pythonOnly = process.argv.includes("--python-only");
const nodeOnly = process.argv.includes("--node-only");
const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };

function run(command, args) {
  process.stdout.write(`\n$ ${command} ${args.join(" ")}\n`);
  const result = spawnSync(command, args, { cwd: root, env, stdio: "inherit" });
  if (result.error) {
    process.stderr.write(`${result.error.message}\n`);
    process.exit(1);
  }
  if (result.status !== 0) process.exit(result.status ?? 1);
}

if (!nodeOnly) {
  const python = resolvePython();
  if (!python) {
    process.stderr.write(`${pythonErrorMessage()}\n`);
    process.exit(1);
  }
  run(python.command, [...python.prefix, "-m", "unittest", "discover", "-s", "tests/dsh", "-v"]);
}

if (!pythonOnly) {
  run(process.execPath, [
    "--test",
    "./tests/controller.test.mjs",
    "./tests/mcp.test.mjs",
    "./tests/workflow-core.test.mjs",
    "./tests/verification.test.mjs",
    "./tests/routing-policy.test.mjs",
    "./tests/install.test.mjs",
    "./tests/controller-link.test.mjs",
    "./tests/team-controller-link.test.mjs",
  ]);
}
