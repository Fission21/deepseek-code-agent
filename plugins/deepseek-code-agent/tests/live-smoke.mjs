import { execFileSync } from "node:child_process";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

import { DeepSeekController } from "../src/controller.mjs";

const source = await fs.mkdtemp(path.join(os.tmpdir(), "deepseek-agent-live-source-"));
const stateRoot = await fs.mkdtemp(path.join(os.tmpdir(), "deepseek-agent-live-state-"));
const controller = new DeepSeekController({ stateRoot });
let agentID;

try {
  execFileSync("git", ["init", source], { stdio: "ignore" });
  execFileSync("git", ["-C", source, "config", "user.email", "smoke@example.invalid"]);
  execFileSync("git", ["-C", source, "config", "user.name", "Smoke Test"]);
  await fs.mkdir(path.join(source, "docs", "rules"), { recursive: true });
  await fs.writeFile(
    path.join(source, "AGENTS.md"),
    "Do not modify files for the live smoke test.\n",
  );
  await fs.writeFile(
    path.join(source, "docs", "rules", "smoke.md"),
    "Return the requested readiness marker.\n",
  );
  await fs.writeFile(path.join(source, "README.md"), "smoke\n");
  execFileSync("git", ["-C", source, "add", "AGENTS.md", "README.md", "docs/rules/smoke.md"]);
  execFileSync("git", ["-C", source, "commit", "-m", "smoke"], { stdio: "ignore" });

  const started = await controller.spawnAgent({
    task:
      "Do not modify files and do not run external network commands. Acknowledge the instruction manifest, then end with BRIDGE_READY.",
    workspace: source,
    workspace_mode: "worktree",
    title: "DeepSeek bridge live smoke test",
    scope_paths: ["README.md"],
    required_reads: ["docs/rules/smoke.md"],
    critical_constraints: ["Do not modify files."],
  });
  if (started.instruction_manifest.files.length !== 2) {
    throw new Error(`Unexpected instruction manifest: ${JSON.stringify(started.instruction_manifest)}`);
  }
  agentID = started.agent_id;
  let cursor = started.cursor;
  let result;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    result = await controller.waitAgent({
      agent_id: agentID,
      cursor,
      timeout_ms: 20_000,
    });
    cursor = result.cursor;
    if (result.state === "completed" || result.state === "needs_attention") break;
  }
  if (result?.state !== "completed") {
    throw new Error(`Live smoke did not complete: ${JSON.stringify(result)}`);
  }
  const text = result.messages
    .flatMap((message) => message.parts)
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n");
  if (!text.includes("BRIDGE_READY")) {
    throw new Error(`Unexpected live response: ${text}`);
  }
  for (const file of started.instruction_manifest.files) {
    if (!text.includes(file.path) || !text.includes(file.sha256)) {
      throw new Error(`Live response did not acknowledge ${file.path} and its hash: ${text}`);
    }
  }
  process.stdout.write(
    `${JSON.stringify({ ok: true, agent_id: agentID, response: "BRIDGE_READY" }, null, 2)}\n`,
  );
} finally {
  if (agentID) {
    await controller.closeAgent({ agent_id: agentID, remove_worktree: true }).catch(() => {});
  }
  controller.shutdown();
  await fs.rm(source, { recursive: true, force: true });
  await fs.rm(stateRoot, { recursive: true, force: true });
}
