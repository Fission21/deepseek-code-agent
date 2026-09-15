import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";

import {
  captureSnapshot,
  computeSnapshotDigest,
  executeCheck,
  outOfScopePaths,
  parsePorcelainStatus,
  resolveCheckCwd,
  snapshotChangedPaths,
} from "../src/verification.mjs";
import { DeepSeekController } from "../src/controller.mjs";

const AGENT_ID = "ses_verify";
const exec = promisify(execFile);
async function git(root, ...args) {
  return await exec("git", ["-C", root, ...args], { windowsHide: true });
}
async function initGit(root) {
  await git(root, "init", "-q");
  await git(root, "add", ".");
  await git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture");
}

function userMessage(id = "msg_user", text = "Please implement the change.") {
  return {
    info: { id, role: "user", time: { created: 1 } },
    parts: [{ type: "text", text }],
  };
}

test("parsePorcelainStatus parses staged, unstaged, deleted and untracked entries", () => {
  const parsed = parsePorcelainStatus(
    [
      "M  tracked/staged.txt",
      " M tracked/modified.txt",
      "D  tracked/gone.txt",
      "?? untracked/new.txt",
      "",
    ].join("\0"),
  );
  assert.deepEqual(parsed, [
    { untracked: false, path: "tracked/staged.txt" },
    { untracked: false, path: "tracked/modified.txt" },
    { untracked: false, path: "tracked/gone.txt" },
    { untracked: true, path: "untracked/new.txt" },
  ]);
  assert.equal(parsePorcelainStatus("garbage without null layout"), null);
  assert.deepEqual(parsePorcelainStatus(""), []);
});

test("captureSnapshot git coverage hashes HEAD, staged/unstaged and untracked content", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "ds-git-snap-"));
  t.after(async () => await fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, "tracked"), { recursive: true });
  await fs.mkdir(path.join(root, "untracked"), { recursive: true });
  await fs.writeFile(path.join(root, "tracked", "staged.txt"), "staged body");
  await fs.writeFile(path.join(root, "tracked", "modified.txt"), "modified body");
  await fs.writeFile(path.join(root, "untracked", "new.txt"), "new body");
  const porcelain = [
    "M  tracked/staged.txt",
    " M tracked/modified.txt",
    "D  tracked/gone.txt",
    "?? untracked/new.txt",
    "",
  ].join("\0");
  const gitRunner = async (args) => {
    if (args[0] === "rev-parse") return { code: 0, stdout: "abc123head\n", stderr: "" };
    if (args[0] === "status") return { code: 0, stdout: porcelain };
    if (args[0] === "ls-files") return { code: 0, stdout: "100644 abc123 0\ttracked/staged.txt\0" };
    throw new Error(`unexpected git args: ${args.join(" ")}`);
  };
  const snapshot = await captureSnapshot({
    root,
    gitRunner,
    taskSpec: { version: 1, checks: [] },
  });
  assert.equal(snapshot.coverage, "git");
  assert.equal(snapshot.head, "abc123head");
  assert.equal(snapshot.entries["tracked/gone.txt"].hash, null);
  assert.equal(snapshot.entries["tracked/gone.txt"].tracked, true);
  assert.equal(snapshot.entries["untracked/new.txt"].tracked, false);
  const { createHash } = await import("node:crypto");
  assert.equal(
    snapshot.entries["tracked/staged.txt"].hash,
    createHash("sha256").update("staged body").digest("hex"),
  );
  assert.equal(
    snapshot.entries["tracked/modified.txt"].hash,
    createHash("sha256").update("modified body").digest("hex"),
  );
  assert.equal(snapshot.digest, computeSnapshotDigest(snapshot));
});

test("captureSnapshot declared_scope hashes scope files and required instructions", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "ds-scope-snap-"));
  t.after(async () => await fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, "src", "lib"), { recursive: true });
  await fs.writeFile(path.join(root, "src", "main.py"), "print('hi')");
  await fs.writeFile(path.join(root, "src", "lib", "util.py"), "value = 1");
  await fs.writeFile(path.join(root, "AGENTS.md"), "rules");
  const stateRoot = await fs.mkdtemp(path.join(os.tmpdir(), "ds-snap-state-"));
  t.after(async () => await fs.rm(stateRoot, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, "state"), { recursive: true });
  await fs.writeFile(path.join(root, "state", "ledger.json"), "must be excluded");
  const manifest = {
    files: [{ path: "src/main.py", sha256: "x", bytes: 0, sources: [], sections: [] }],
    total_bytes: 0,
  };
  const snapshot = await captureSnapshot({
    root,
    scopePaths: ["src"],
    taskSpec: { version: 1, design_decisions: ["d"], checks: [] },
    instructionManifest: manifest,
    stateRoot: path.join(root, "state"),
  });
  assert.equal(snapshot.coverage, "declared_scope");
  assert.ok(snapshot.entries["src/main.py"]);
  assert.ok(snapshot.entries["src/lib/util.py"]);
  assert.ok(snapshot.entries["src/AGENTS.md"] === undefined);
  assert.ok(!("state/ledger.json" in snapshot.entries));
  assert.equal(snapshot.instruction_hashes["src/main.py"], snapshot.entries["src/main.py"].hash);
  const before = snapshot.digest;
  await fs.writeFile(path.join(root, "state", "ledger.json"), "changed outside scope");
  const again = await captureSnapshot({
    root,
    scopePaths: ["src"],
    taskSpec: { version: 1, design_decisions: ["d"], checks: [] },
    instructionManifest: manifest,
    stateRoot: path.join(root, "state"),
  });
  assert.equal(again.digest, before);
});

test("captureSnapshot with an empty scope on a nongit workspace reports unavailable", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "ds-empty-snap-"));
  t.after(async () => await fs.rm(root, { recursive: true, force: true }));
  const snapshot = await captureSnapshot({ root, scopePaths: [] });
  assert.equal(snapshot.coverage, "unavailable");
  assert.equal(snapshot.digest, null);
  assert.match(snapshot.note, /non-empty scope_paths/);
});

test("symlinked files are hashed by link target and never read through", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "ds-symlink-snap-"));
  t.after(async () => await fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, "src"), { recursive: true });
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), "ds-outside-"));
  t.after(async () => await fs.rm(outside, { recursive: true, force: true }));
  const outsideFile = path.join(outside, "secret.txt");
  await fs.writeFile(outsideFile, "secret content v1");
  const createSymlink = fs.symlink;
  try {
    await createSymlink(outsideFile, path.join(root, "src", "link.txt"), "file");
  } catch (error) {
    if (error?.code === "EPERM") {
      t.skip("Windows symlink privilege unavailable");
      return;
    }
    throw error;
  }
  const snapshot = await captureSnapshot({ root, scopePaths: ["src"] });
  const linkedHash = snapshot.entries["src/link.txt"].hash;
  const { createHash } = await import("node:crypto");
  assert.equal(linkedHash, createHash("sha256").update(`symlink:${await fs.readlink(path.join(root, "src", "link.txt"))}`).digest("hex"));
  const before = snapshot.digest;
  await fs.writeFile(outsideFile, "secret content v2");
  const after = await captureSnapshot({ root, scopePaths: ["src"] });
  assert.equal(after.digest, before);
});

test("executeCheck runs real commands with exit codes and full log retention", async (t) => {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "ds-check-ws-"));
  t.after(async () => await fs.rm(workspace, { recursive: true, force: true }));
  const logPath = path.join(workspace, "ok.log");
  const big = "A".repeat(4000);
  const ok = await executeCheck({
    check: {
      id: "ok",
      argv: [process.execPath, "-e", `console.log("line1:${big}")`],
      cwd: ".",
      timeout_ms: 60000,
    },
    workspaceRoot: workspace,
    logPath,
  });
  assert.equal(ok.exit_code, 0);
  assert.equal(ok.timed_out, false);
  assert.equal(ok.refused, false);
  const logContent = await fs.readFile(logPath, "utf8");
  assert.match(logContent, /^line1:/);
  assert.ok(logContent.length >= 4000);

  const failed = await executeCheck({
    check: { id: "fail", argv: [process.execPath, "-e", "process.exit(3)"], cwd: ".", timeout_ms: 60000 },
    workspaceRoot: workspace,
    logPath: path.join(workspace, "fail.log"),
  });
  assert.equal(failed.exit_code, 3);
  assert.equal(failed.timed_out, false);
});

test("executeCheck honors timeouts and preserves partial logs", async (t) => {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "ds-check-ws-"));
  t.after(async () => await fs.rm(workspace, { recursive: true, force: true }));
  const logPath = path.join(workspace, "slow.log");
  const result = await executeCheck({
    check: {
      id: "slow",
      argv: [
        process.execPath,
        "-e",
        'console.log("started"); setTimeout(() => console.log("never"), 10000)',
      ],
      cwd: ".",
      timeout_ms: 300,
    },
    workspaceRoot: workspace,
    logPath,
  });
  assert.equal(result.timed_out, true);
  const logContent = await fs.readFile(logPath, "utf8");
  assert.match(logContent, /started/);
  assert.doesNotMatch(logContent, /never/);
});

test("executeCheck refuses Windows .cmd/.bat without spawning", async (t) => {
  if (process.platform !== "win32") {
    const refused = await executeCheck({
      check: { id: "shim", argv: ["opencode.cmd", "--version"], cwd: ".", timeout_ms: 1000 },
      workspaceRoot: process.cwd(),
      logPath: path.join(os.tmpdir(), "ds-noop.log"),
      platform: "win32",
      spawnFn: () => {
        throw new Error("must not spawn");
      },
      terminateFn: () => {},
    });
    assert.equal(refused.refused, true);
    assert.match(refused.error, /native executable/);
    return;
  }
  const spawnCalls = [];
  const refused = await executeCheck({
    check: { id: "shim", argv: ["tools/run.cmd"], cwd: ".", timeout_ms: 1000 },
    workspaceRoot: process.cwd(),
    logPath: path.join(os.tmpdir(), "ds-noop.log"),
    spawnFn: (...args) => {
      spawnCalls.push(args);
      throw new Error("must not spawn");
    },
    terminateFn: () => {},
  });
  assert.equal(refused.refused, true);
  assert.match(refused.error, /native executable/);
  assert.deepEqual(spawnCalls, []);
});

test("executeCheck rejects a cwd that escapes through a symlink", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "ds-cwd-escape-"));
  t.after(async () => await fs.rm(root, { recursive: true, force: true }));
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), "ds-cwd-out-"));
  t.after(async () => await fs.rm(outside, { recursive: true, force: true }));
  try {
    await fs.symlink(outside, path.join(root, "escape"), process.platform === "win32" ? "junction" : "dir");
  } catch (error) {
    if (error?.code === "EPERM") {
      t.skip("Windows symlink privilege unavailable");
      return;
    }
    throw error;
  }
  await assert.rejects(resolveCheckCwd(root, "escape"), /escapes the workspace/);
  const result = await executeCheck({
    check: { id: "escaped", argv: [process.execPath, "-e", "process.exit(0)"], cwd: "escape", timeout_ms: 5000 },
    workspaceRoot: root,
    logPath: path.join(root, "escaped.log"),
  });
  assert.equal(result.exit_code, null);
  assert.match(result.error, /escapes the workspace/);
});

test("outOfScopePaths honors dot and nested scope entries", () => {
  assert.deepEqual(
    outOfScopePaths(["src/a.py", "docs/b.md", "top.txt"], ["src"]),
    ["docs/b.md", "top.txt"],
  );
  assert.deepEqual(outOfScopePaths(["anything"], ["."]), []);
});

async function verifyController(t, { status = () => ({ type: "idle" }), spec = null } = {}) {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "ds-verify-ws-"));
  t.after(async () => await fs.rm(workspace, { recursive: true, force: true }));
  const stateRoot = await fs.mkdtemp(path.join(os.tmpdir(), "ds-verify-state-"));
  t.after(async () => await fs.rm(stateRoot, { recursive: true, force: true }));
  await fs.mkdir(path.join(workspace, "src"), { recursive: true });
  await fs.writeFile(path.join(workspace, "src", "main.py"), "value = 1\n");
  const agentID = AGENT_ID;
  const submitted = [];
  let spawned = 0;
  const controller = new DeepSeekController({
    stateRoot,
    spawn: () => {
      throw new Error("opencode spawn must not be called in offline tests");
    },
  });
  controller.server = { url: "http://127.0.0.1:4096", authorization: "Basic test", child: null };
  controller.request = async (method, endpoint, options = {}) => {
    if (endpoint === "/session/status") {
      return { [agentID]: typeof status === "function" ? status() : status };
    }
    if (endpoint.endsWith("/permission") || endpoint.endsWith("/question")) return [];
    if (endpoint.endsWith("/message")) return [userMessage("msg_task")];
    if (endpoint.endsWith("/prompt_async")) {
      submitted.push(options.body?.parts?.[0]?.text ?? "");
      return {};
    }
    if (endpoint.endsWith("/fork")) return { id: "ses_child", title: "fork" };
    if (endpoint.endsWith("/abort")) return {};
    throw new Error(`Unexpected offline request: ${method} ${endpoint}`);
  };
  await controller.writeState({
    agent_id: agentID,
    status: "active",
    directory: workspace,
    source_root: workspace,
    workspace_mode: "current",
    temp_root: null,
    owns_worktree: false,
    created_at: 1,
    title: "verify fixture",
    scope_paths: ["src"],
    instruction_manifest: { files: [], total_bytes: 0 },
    task_spec: spec,
    source_snapshot: null,
    verify: null,
  });
  if (spec) {
    const state = await controller.readState(agentID);
    state.source_snapshot = await controller.captureSourceSnapshot({
      root: workspace, scopePaths: state.scope_paths, taskSpec: spec,
      instructionManifest: state.instruction_manifest,
    });
    await controller.writeState(state);
  }
  return { controller, agentID, workspace, submitted };
}

const PASS_CHECK = {
  id: "unit",
  argv: [process.execPath, "-e", "process.exit(0)"],
  cwd: ".",
  timeout_ms: 60000,
};

function specWith(checks) {
  return {
    version: 1,
    design_decisions: ["Single pass, no deps."],
    acceptance_criteria: ["npm test passes."],
    checks,
  };
}

test("verifyAgent runs persisted checks, caches evidence and never re-runs from queries", async (t) => {
  const { controller, agentID, workspace, submitted } = await verifyController(t, {
    spec: specWith([PASS_CHECK]),
  });
  let spawnCalls = 0;
  const baseSpawn = controller.checkSpawn.bind(controller);
  controller.checkSpawn = (...args) => {
    spawnCalls += 1;
    return baseSpawn(...args);
  };

  const first = await controller.verifyAgent({ agent_id: agentID, wait_ms: 55000 });
  assert.equal(first.status, "passed");
  assert.equal(first.results[0].exit_code, 0);
  assert.equal(first.evidence.verified, true);
  assert.equal(spawnCalls, 1);
  assert.ok(first.results[0].log_path);
  const logExists = await fs.stat(first.results[0].log_path).catch(() => null);
  assert.ok(logExists);
  assert.match(first.note, /Codex review/);
  assert.equal(first.results[0].command, PASS_CHECK.argv.join(" "));

  const repeat = await controller.verifyAgent({ agent_id: agentID });
  assert.equal(repeat.job_id, first.job_id);
  assert.equal(repeat.status, "passed");
  assert.equal(spawnCalls, 1, "repeated verify must reuse the completed job");

  const state = await controller.readState(agentID);
  assert.equal(state.verify.evidence.job_id, first.job_id);

  const inspected = await controller.inspectAgent({ agent_id: agentID });
  assert.equal(spawnCalls, 1, "inspect must never execute checks");
  assert.equal(inspected.review.verification.evidence_cached, true);
  assert.equal(inspected.review.verification.evidence_current, true);
  assert.deepEqual(inspected.review.changed_paths, []);
  assert.match(inspected.review.note, /Codex review/);

  const rerun = await controller.verifyAgent({ agent_id: agentID, rerun: true, wait_ms: 55000 });
  assert.notEqual(rerun.job_id, first.job_id);
  assert.equal(rerun.status, "passed");
  assert.equal(spawnCalls, 2);
  void workspace;
  void submitted;
});

test("verifyAgent refuses non-idle, queued and pending workers", async (t) => {
  const { controller, agentID } = await verifyController(t, {
    status: () => ({ type: "busy" }),
    spec: specWith([PASS_CHECK]),
  });
  await assert.rejects(
    controller.verifyAgent({ agent_id: agentID }),
    /must be idle before verification/,
  );

  const idle = await verifyController(t, {
    status: () => ({ type: "idle" }),
    spec: specWith([PASS_CHECK]),
  });
  await idle.controller.enqueue(idle.agentID, "pending follow-up");
  await assert.rejects(
    idle.controller.verifyAgent({ agent_id: idle.agentID }),
    /queued/,
  );

  const pend = await verifyController(t, {
    status: () => ({ type: "idle" }),
    spec: specWith([PASS_CHECK]),
  });
  pend.controller.pending = async () => ({ permissions: [{ id: "per_1" }], questions: [] });
  await assert.rejects(
    pend.controller.verifyAgent({ agent_id: pend.agentID }),
    /pending permission/,
  );
});

test("a failing check is reported with its real exit code and no evidence is cached", async (t) => {
  const { controller, agentID } = await verifyController(t, {
    spec: specWith([{ id: "fail", argv: [process.execPath, "-e", "process.exit(2)"], cwd: ".", timeout_ms: 60000 }]),
  });
  const job = await controller.verifyAgent({ agent_id: agentID, wait_ms: 55000 });
  assert.equal(job.status, "failed");
  assert.equal(job.results[0].exit_code, 2);
  assert.equal(job.evidence.verified, false);
  const state = await controller.readState(agentID);
  assert.equal(state.verify.latest_status, "failed");
  assert.equal(state.verify.evidence, undefined);
  const review = (await controller.inspectAgent({ agent_id: agentID })).review;
  assert.ok(review.verification.evidence_path);
  assert.notEqual(review.verification.checks[0].exit_code, 0);
  assert.ok(review.verification.checks[0].log_path);
  assert.ok(review.unresolved.length > 0);
});

test("checks that mutate the source tree cannot produce valid evidence", async (t) => {
  const { controller, agentID } = await verifyController(t, {
    spec: specWith([
      {
        id: "mutator",
        argv: [
          process.execPath,
          "-e",
          'require("node:fs").writeFileSync("src/main.py", "value = 2\\n")',
        ],
        cwd: ".",
        timeout_ms: 60000,
      },
    ]),
  });
  const job = await controller.verifyAgent({ agent_id: agentID, wait_ms: 55000 });
  assert.equal(job.status, "passed");
  assert.equal(job.evidence.digest_unchanged, false);
  assert.equal(job.evidence.verified, false);
  assert.match(job.evidence.reasons.join(" "), /changed the source tree/);
  const state = await controller.readState(agentID);
  assert.equal(state.verify.evidence, undefined);
});

test("new out-of-scope changes against the spawn snapshot invalidate evidence", async (t) => {
  const { controller, agentID, workspace } = await verifyController(t, {
    spec: specWith([PASS_CHECK]),
  });
  await initGit(workspace);
  const stateBefore = await controller.readState(agentID);
  stateBefore.source_snapshot = await controller.captureSourceSnapshot({
    root: workspace,
    scopePaths: ["src"],
    taskSpec: stateBefore.task_spec,
    instructionManifest: stateBefore.instruction_manifest,
  });
  await controller.writeState(stateBefore);
  await fs.writeFile(path.join(workspace, "rogue.txt"), "outside scope");
  const job = await controller.verifyAgent({ agent_id: agentID, wait_ms: 55000 });
  assert.equal(job.status, "passed");
  assert.equal(job.evidence.verified, false);
  assert.ok(job.evidence.out_of_scope_changes.includes("rogue.txt"));
});

test("stale evidence flips current=false after source or instruction changes", async (t) => {
  const { controller, agentID, workspace } = await verifyController(t, {
    spec: specWith([PASS_CHECK]),
  });
  const state = await controller.readState(agentID);
  state.instruction_manifest = {
    files: [{ path: "src/main.py", sha256: "x", bytes: 0, sources: [], sections: [] }],
    total_bytes: 0,
  };
  await controller.writeState(state);
  const first = await controller.verifyAgent({ agent_id: agentID, wait_ms: 55000 });
  assert.equal(first.evidence.verified, true);
  const fresh = await controller.inspectAgent({ agent_id: agentID });
  assert.equal(fresh.review.verification.evidence_current, true);

  await fs.writeFile(path.join(workspace, "src", "main.py"), "value = 2\n");
  const stale = await controller.inspectAgent({ agent_id: agentID });
  assert.equal(stale.review.verification.evidence_current, false);
  assert.ok(stale.review.changed_paths.includes("src/main.py"));
});

test("legacy sessions without task_spec still inspect and refuse verification", async (t) => {
  const { controller, agentID } = await verifyController(t);
  const inspected = await controller.inspectAgent({ agent_id: agentID });
  assert.equal(inspected.review, undefined);
  await assert.rejects(
    controller.verifyAgent({ agent_id: agentID }),
    /no persisted task_spec checks/,
  );
});

test("concurrent verify calls share one job and run checks exactly once", async (t) => {
  const { controller, agentID } = await verifyController(t, {
    spec: specWith([
      {
        id: "slowish",
        argv: [process.execPath, "-e", 'setTimeout(() => process.exit(0), 150)'],
        cwd: ".",
        timeout_ms: 60000,
      },
    ]),
  });
  let spawnCalls = 0;
  const baseSpawn = controller.checkSpawn.bind(controller);
  controller.checkSpawn = (...args) => {
    spawnCalls += 1;
    return baseSpawn(...args);
  };
  const [first, second] = await Promise.all([
    controller.verifyAgent({ agent_id: agentID, wait_ms: 55000 }),
    controller.verifyAgent({ agent_id: agentID, wait_ms: 55000 }),
  ]);
  assert.equal(first.job_id, second.job_id);
  assert.equal(first.status, "passed");
  assert.equal(spawnCalls, 1);
});

test("writes and queue flushes are blocked while verification runs", async (t) => {
  const { controller, agentID } = await verifyController(t, {
    spec: specWith([
      {
        id: "slowcheck",
        argv: [process.execPath, "-e", 'setTimeout(() => process.exit(0), 800)'],
        cwd: ".",
        timeout_ms: 60000,
      },
    ]),
  });
  await controller.verifyAgent({ agent_id: agentID });
  await assert.rejects(
    controller.sendMessage({ agent_id: agentID, message: "nope" }),
    /ds_verify_agent is running/,
  );
  await assert.rejects(
    controller.forkAgent({ agent_id: agentID }),
    /ds_verify_agent is running/,
  );
  await controller.enqueue(agentID, "queued while verifying");
  const wait = await controller.waitAgent({ agent_id: agentID, timeout_ms: 0 });
  assert.equal(wait.state, "timed_out");
  assert.equal(await controller.queueCount(agentID), 1);
  const job = await controller.verifyAgent({ agent_id: agentID, wait_ms: 55000 });
  assert.equal(job.status, "passed");
  const drained = await controller.waitAgent({ agent_id: agentID, timeout_ms: 0 });
  assert.equal(drained.state, "timed_out");
  assert.equal(await controller.queueCount(agentID), 0);
  await controller.sendMessage({ agent_id: agentID, message: "now allowed" });
  assert.equal(await controller.queueCount(agentID), 0);
});

test("interrupting cancels verification, waits for the child, and preserves logs", async (t) => {
  const { controller, agentID, workspace } = await verifyController(t, {
    spec: specWith([
      {
        id: "longcheck",
        argv: [process.execPath, "-e", 'console.log("tick"); setTimeout(() => process.exit(0), 30000)'],
        cwd: ".",
        timeout_ms: 60000,
      },
    ]),
  });
  const started = await controller.verifyAgent({ agent_id: agentID });
  assert.equal(started.status, "running");
  await new Promise((resolve) => setTimeout(resolve, 150));
  const interrupted = await controller.interruptAgent({ agent_id: agentID });
  assert.equal(interrupted.state, "interrupted");
  const job = await controller.loadLatestJob(agentID);
  assert.equal(job.status, "cancelled");
  const logContent = await fs.readFile(job.results[0].log_path, "utf8");
  assert.match(logContent, /tick/);
  void workspace;
});

test("closeAgent blocks queued delivery and later flushes of the old queue", async (t) => {
  const { controller, agentID } = await verifyController(t, {
    spec: specWith([PASS_CHECK]),
  });
  await controller.enqueue(agentID, "old queued message");
  await new Promise((resolve) => setTimeout(resolve, 5));
  const closed = await controller.closeAgent({ agent_id: agentID });
  assert.equal(closed.state, "closed");
  await assert.rejects(
    controller.sendMessage({ agent_id: agentID, message: "too late" }),
    /closed/,
  );
  const wait = await controller.waitAgent({ agent_id: agentID, timeout_ms: 0 });
  assert.equal(wait.state, "timed_out");
  assert.equal(await controller.queueCount(agentID), 1);
  await assert.rejects(
    controller.verifyAgent({ agent_id: agentID }),
    /closed/,
  );
});

test("closeAgent persists verified evidence before marking closed", async (t) => {
  const { controller, agentID } = await verifyController(t, {
    spec: specWith([PASS_CHECK]),
  });
  const job = await controller.verifyAgent({ agent_id: agentID, wait_ms: 55000 });
  assert.equal(job.status, "passed");
  const closed = await controller.closeAgent({ agent_id: agentID });
  assert.equal(closed.state, "closed");
  const state = await controller.readState(agentID);
  assert.equal(state.status, "closed");
  assert.equal(state.verify.evidence.job_id, job.job_id);
});

test("real Git snapshots use the worker directory and detect index-only changes", async (t) => {
  const { controller, agentID, workspace } = await verifyController(t, { spec: specWith([PASS_CHECK]) });
  await initGit(workspace);
  const file = path.join(workspace, "src/main.py");
  await fs.writeFile(file, "stage one");
  await git(workspace, "add", ".");
  await fs.writeFile(file, "working tree stays fixed");
  const args = { root: workspace, scopePaths: ["src"], taskSpec: specWith([PASS_CHECK]) };
  const before = await controller.captureSourceSnapshot(args);
  assert.equal(before.coverage, "git");
  assert.deepEqual(Object.keys(before.entries), ["src/main.py"]);
  await fs.writeFile(file, "stage two");
  await git(workspace, "add", ".");
  await fs.writeFile(file, "working tree stays fixed");
  const after = await controller.captureSourceSnapshot(args);
  assert.notEqual(after.digest, before.digest);
  assert.deepEqual(snapshotChangedPaths(before, after), ["src/main.py"]);
  const state = await controller.readState(agentID);
  state.source_snapshot = after;
  await controller.writeState(state);
  const passed = await controller.verifyAgent({ agent_id: agentID, wait_ms: 55000 });
  assert.equal(passed.evidence.current, true);
  await fs.writeFile(file, "after verification");
  const stale = await controller.verifyAgent({ agent_id: agentID });
  assert.equal(stale.job_id, passed.job_id);
  assert.equal(stale.evidence.current, false);
  assert.equal(stale.evidence.verified, false);
});

test("a later failed verification cannot reuse an earlier successful result", async (t) => {
  const { controller, agentID } = await verifyController(t, { spec: specWith([PASS_CHECK]) });
  assert.equal((await controller.verifyAgent({ agent_id: agentID, wait_ms: 55000 })).evidence.verified, true);
  controller.checkSpawn = () => { throw new Error("spawn failed"); };
  const failed = await controller.verifyAgent({ agent_id: agentID, rerun: true, wait_ms: 55000 });
  assert.equal(failed.status, "failed");
  assert.equal((await controller.inspectAgent({ agent_id: agentID })).review.verification.evidence_current, false);
});

test("directory junctions cannot make scope traversal read outside the workspace", async (t) => {
  const { workspace } = await verifyController(t);
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), "ds-junction-target-"));
  t.after(() => fs.rm(outside, { recursive: true, force: true }));
  await fs.writeFile(path.join(outside, "file.txt"), "private");
  await fs.symlink(outside, path.join(workspace, "link"), process.platform === "win32" ? "junction" : "dir");
  const before = await captureSnapshot({ root: workspace, scopePaths: ["link"] });
  assert.deepEqual(Object.keys(before.entries), ["link"]);
  await fs.writeFile(path.join(outside, "file.txt"), "changed outside");
  assert.equal((await captureSnapshot({ root: workspace, scopePaths: ["link"] })).digest, before.digest);
  await assert.rejects(captureSnapshot({ root: workspace, scopePaths: ["link/file.txt"] }), /escapes workspace/);
  await assert.rejects(resolveCheckCwd(workspace, "link"), /escapes/);
});

test("check logs preserve split UTF-8 and log write failures fail the check", async (t) => {
  const { workspace } = await verifyController(t);
  const logPath = path.join(workspace, "raw.log");
  const bytes = Buffer.from("完整中文输出");
  const spawnFn = () => {
    const child = new EventEmitter();
    child.stdout = new PassThrough(); child.stderr = new PassThrough();
    queueMicrotask(() => {
      child.stdout.write(bytes.subarray(0, 1));
      child.stdout.write(bytes.subarray(1));
      child.emit("close", 0);
    });
    return child;
  };
  const result = await executeCheck({ check: PASS_CHECK, workspaceRoot: workspace, logPath, spawnFn });
  assert.equal(result.exit_code, 0);
  assert.deepEqual(await fs.readFile(logPath), bytes);
  const failed = await executeCheck({ check: PASS_CHECK, workspaceRoot: workspace, logPath, spawnFn,
    openLog: async () => ({ write: async () => { throw new Error("disk full"); }, close: async () => {} }),
  });
  assert.equal(failed.exit_code, 0);
  assert.match(failed.error, /log I\/O failed.*disk full/);
});

test("cancellation during async setup prevents spawning and process errors await close", async (t) => {
  const { workspace } = await verifyController(t);
  let cancelled = false; let spawns = 0;
  const logPath = path.join(workspace, "cancel.log");
  const result = await executeCheck({ check: PASS_CHECK, workspaceRoot: workspace, logPath,
    isCancelled: () => cancelled, spawnFn: () => { spawns++; },
    openLog: async (name) => { cancelled = true; return fs.open(name, "w"); },
  });
  assert.equal(spawns, 0); assert.equal(result.cancelled, true);
  const child = new EventEmitter();
  child.stdout = new PassThrough(); child.stderr = new PassThrough();
  let ready; const childReady = new Promise(resolve => { ready = resolve; });
  let settled = false;
  const running = executeCheck({ check: PASS_CHECK, workspaceRoot: workspace, logPath,
    spawnFn: () => child, onChild: () => ready(),
  }).then(value => { settled = true; return value; });
  await childReady;
  child.emit("error", new Error("process error"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(settled, false);
  child.emit("close", 1);
  assert.equal((await running).exit_code, 1);
});

test("verification cannot start halfway through a follow-up submission", async (t) => {
  let busy = false;
  const { controller, agentID } = await verifyController(t, {
    spec: specWith([PASS_CHECK]), status: () => ({ type: busy ? "busy" : "idle" }),
  });
  let release; const gate = new Promise(resolve => { release = resolve; });
  let entered; const inSubmit = new Promise(resolve => { entered = resolve; });
  const request = controller.request.bind(controller);
  controller.request = async (method, endpoint, options) => {
    if (endpoint.endsWith("/prompt_async")) { entered(); await gate; busy = true; }
    return request(method, endpoint, options);
  };
  const send = controller.sendMessage({ agent_id: agentID, message: "follow up" });
  await inSubmit;
  const verification = controller.verifyAgent({ agent_id: agentID });
  const rejection = assert.rejects(verification, /must be idle/);
  release(); await send; await rejection;
  assert.equal(controller.verifyJobs.size, 0);
});
