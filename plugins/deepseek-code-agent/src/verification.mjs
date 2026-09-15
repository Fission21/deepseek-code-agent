import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";

export function terminateChild(child) {
  if (!child || child.killed) return;
  if (process.platform === "win32" && child.pid) {
    const killer = spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    });
    killer.on("error", () => child.kill("SIGTERM"));
    killer.unref();
    return;
  }
  child.kill("SIGTERM");
}

export function sha256Text(data) {
  return createHash("sha256").update(data).digest("hex");
}

export function sha256Buffer(data) {
  return createHash("sha256").update(data).digest("hex");
}

export function toPosix(value) {
  return value.split(path.sep).join("/");
}

export function isInsidePath(rootReal, target) {
  const relative = path.relative(rootReal, target);
  return (
    relative === "" ||
    (relative !== ".." &&
      !relative.startsWith(`..${path.sep}`) &&
      !path.isAbsolute(relative))
  );
}

export function computeSnapshotDigest(snapshot) {
  return sha256Text(
    JSON.stringify({
      coverage: snapshot.coverage,
      head: snapshot.head ?? null,
      index: snapshot.index_entries ?? {},
      entries: Object.entries(snapshot.entries ?? {}).sort(([a], [b]) =>
        a < b ? -1 : a > b ? 1 : 0,
      ),
      instruction_files: snapshot.instruction_hashes ?? {},
      instruction_identity: snapshot.manifest_digest ?? null,
      task_spec: snapshot.spec_digest ?? null,
    }),
  );
}

export function parsePorcelainStatus(text) {
  if (typeof text !== "string") return null;
  const entries = [];
  for (const token of text.split("\0")) {
    if (!token) continue;
    if (token.length < 4 || token[2] !== " ") return null;
    entries.push({
      untracked: token.startsWith("??"),
      path: toPosix(token.slice(3)),
    });
  }
  return entries;
}

export async function hashSnapshotFile(rootReal, absolutePath) {
  if (!isInsidePath(rootReal, absolutePath)) throw new Error("snapshot path escapes workspace");
  let stat;
  try {
    stat = await fs.lstat(absolutePath);
  } catch (error) {
    if (error?.code === "ENOENT") return { hash: null };
    throw error;
  }
  const parent = await fs.realpath(path.dirname(absolutePath));
  if (!isInsidePath(rootReal, parent)) throw new Error("snapshot parent escapes workspace");
  if (stat.isSymbolicLink()) {
    const target = await fs.readlink(absolutePath);
    return { hash: sha256Text(`symlink:${target}`) };
  }
  if (!stat.isFile()) return null;
  const real = await fs.realpath(absolutePath);
  if (!isInsidePath(rootReal, real)) throw new Error("snapshot path escapes workspace");
  const content = await fs.readFile(absolutePath);
  return { hash: sha256Buffer(content), bytes: content.byteLength };
}

export async function captureSnapshot({
  root,
  scopePaths = [],
  taskSpec = null,
  instructionManifest = null,
  stateRoot = null,
  gitRunner = null,
}) {
  const rootReal = await fs.realpath(path.resolve(root));
  const stateRootReal = stateRoot ? path.resolve(stateRoot) : null;
  const isExcluded = (relativePath) => {
    if (relativePath === ".git" || relativePath.startsWith(".git/")) return true;
    if (!stateRootReal) return false;
    const absolute = path.resolve(rootReal, ...relativePath.split("/"));
    return isInsidePath(stateRootReal, absolute);
  };

  const hashRelative = async (relativePath) => {
    const absolute = path.resolve(rootReal, ...relativePath.split("/"));
    return await hashSnapshotFile(rootReal, absolute);
  };

  let coverage = null;
  let head = null;
  const entries = {};
  const index_entries = {};

  if (gitRunner) {
    const [headResult, statusResult] = await Promise.all([
      gitRunner(["rev-parse", "HEAD"]),
      gitRunner([
        "status",
        "--porcelain",
        "-z",
        "--untracked-files=all",
        "--no-renames",
      ]),
    ]);
    const parsed =
      headResult.code === 0 && statusResult.code === 0
        ? parsePorcelainStatus(statusResult.stdout)
        : null;
    if (parsed) {
      const indexResult = await gitRunner(["ls-files", "--stage", "-z"]);
      if (indexResult.code !== 0) throw new Error("cannot read Git index for snapshot");
      for (const line of indexResult.stdout.split("\0").filter(Boolean)) {
        const tab = line.indexOf("\t");
        if (tab < 0) throw new Error("invalid Git index record");
        const name = toPosix(line.slice(tab + 1));
        if (!isExcluded(name)) index_entries[name] = `${index_entries[name] ?? ""}${line.slice(0, tab)}\n`;
      }
      coverage = "git";
      head = headResult.stdout.trim() || null;
      for (const entry of parsed) {
        if (isExcluded(entry.path)) continue;
        if (entry.untracked) {
          const hashed = await hashRelative(entry.path);
          if (hashed && hashed.hash !== null) {
            entries[entry.path] = { hash: hashed.hash, tracked: false };
          }
        } else {
          const hashed = await hashRelative(entry.path);
          if (hashed) entries[entry.path] = { hash: hashed.hash, tracked: true };
        }
      }
    }
  }

  if (!coverage) {
    if (!scopePaths.length) {
      return {
        coverage: "unavailable",
        head: null,
        entries: {},
        instruction_hashes: {},
        spec_digest: taskSpec ? sha256Text(JSON.stringify(taskSpec)) : null,
        digest: null,
        note: "not a Git repository; provide a non-empty scope_paths for a declared_scope snapshot",
      };
    }
    coverage = "declared_scope";
    for (const scope of scopePaths) {
      const absoluteScope = path.resolve(rootReal, ...scope.split("/"));
      let stat;
      try {
        if (!isInsidePath(rootReal, absoluteScope)) throw new Error("scope escapes workspace");
        stat = await fs.lstat(absoluteScope);
      } catch (error) {
        if (error?.code === "ENOENT") continue;
        throw error;
      }
      if (stat.isFile() || stat.isSymbolicLink()) {
        const relativeScope = toPosix(path.relative(rootReal, absoluteScope));
        if (isExcluded(relativeScope)) continue;
        const hashed = await hashSnapshotFile(rootReal, absoluteScope);
        if (hashed && hashed.hash !== null) {
          entries[relativeScope] = { hash: hashed.hash };
        }
        continue;
      }
      if (!stat.isDirectory()) continue;
      const queue = [absoluteScope];
      while (queue.length) {
        const directory = queue.pop();
        const realDirectory = await fs.realpath(directory);
        if (!isInsidePath(rootReal, realDirectory)) throw new Error("scope directory escapes workspace");
        const dirents = await fs.readdir(directory, { withFileTypes: true });
        for (const dirent of dirents) {
          const absolute = path.join(directory, dirent.name);
          const relative = toPosix(path.relative(rootReal, absolute));
          if (isExcluded(relative)) continue;
          if (dirent.isDirectory()) {
            if (dirent.name === ".git") continue;
            queue.push(absolute);
          } else if (dirent.isFile() || dirent.isSymbolicLink()) {
            const hashed = await hashSnapshotFile(rootReal, absolute);
            if (hashed && hashed.hash !== null) entries[relative] = { hash: hashed.hash };
          }
        }
      }
    }
  }

  const instruction_hashes = {};
  for (const file of instructionManifest?.files ?? []) {
    const hashed = await hashRelative(file.path);
    instruction_hashes[file.path] = hashed ? hashed.hash : "missing";
  }

  const snapshot = {
    coverage,
    head,
    index_entries,
    entries,
    instruction_hashes,
    manifest_digest: sha256Text(JSON.stringify(instructionManifest)),
    spec_digest: taskSpec ? sha256Text(JSON.stringify(taskSpec)) : null,
  };
  snapshot.digest = computeSnapshotDigest(snapshot);
  return snapshot;
}

export function snapshotChangedPaths(before, after) {
  if (!before?.entries || !after?.entries) return null;
  const keys = new Set([
    ...Object.keys(before.entries),
    ...Object.keys(after.entries),
    ...Object.keys(before.index_entries ?? {}),
    ...Object.keys(after.index_entries ?? {}),
  ]);
  const changed = [];
  for (const key of keys) {
    const beforeHash = before.entries[key]?.hash ?? null;
    const afterHash = after.entries[key]?.hash ?? null;
    if (beforeHash !== afterHash || before.index_entries?.[key] !== after.index_entries?.[key]) changed.push(key);
  }
  return changed.sort();
}

export function pathInScope(relativePath, scopePaths) {
  for (const scope of scopePaths ?? []) {
    if (scope === ".") return true;
    const trimmed = scope.replace(/\/+$/, "");
    if (!trimmed) continue;
    if (relativePath === trimmed || relativePath.startsWith(`${trimmed}/`)) {
      return true;
    }
  }
  return false;
}

export function outOfScopePaths(changedPaths, scopePaths) {
  return (changedPaths ?? []).filter(
    (value) => !pathInScope(value, scopePaths),
  );
}

export async function resolveCheckCwd(workspaceRoot, cwd) {
  const rootReal = await fs.realpath(path.resolve(workspaceRoot));
  const target = path.resolve(rootReal, ...cwd.split("/"));
  let real;
  try {
    real = await fs.realpath(target);
  } catch {
    throw new Error(`check cwd does not exist inside the workspace: ${cwd}`);
  }
  if (!isInsidePath(rootReal, real)) {
    throw new Error(
      `check cwd escapes the workspace through a symlink: ${cwd}`,
    );
  }
  return real;
}

export async function executeCheck({
  check,
  workspaceRoot,
  logPath,
  spawnFn = spawn,
  terminateFn = terminateChild,
  isCancelled,
  onChild,
  openLog = (name) => fs.open(name, "w"),
  platform = process.platform,
}) {
  const started = Date.now();
  const base = {
    id: check.id,
    command: check.argv.join(" "),
    cwd: check.cwd,
    timeout_ms: check.timeout_ms,
  };
  const result = { ...base, exit_code: null, timed_out: false, cancelled: false, refused: false, error: null };

  if (isCancelled?.()) {
    return { ...result, cancelled: true, duration_ms: 0 };
  }

  if (platform === "win32" && /\.(?:cmd|bat)$/i.test(check.argv[0])) {
    return {
      ...result,
      refused: true,
      error:
        "Windows .cmd/.bat checks are refused to avoid shell re-interpretation; invoke the native executable directly (for example node.exe or the real binary path).",
      duration_ms: Date.now() - started,
    };
  }

  let resolvedCwd;
  try {
    resolvedCwd = await resolveCheckCwd(workspaceRoot, check.cwd);
  } catch (error) {
    return {
      ...result,
      error: error instanceof Error ? error.message : String(error),
      duration_ms: Date.now() - started,
    };
  }

  let handle = null;
  let writeChain = Promise.resolve();
  let logError = null;
  let outcome = {};
  try {
    await fs.mkdir(path.dirname(logPath), { recursive: true });
    handle = await openLog(logPath);
    const append = (data) => {
      const bytes = Buffer.isBuffer(data) ? data : Buffer.from(data);
      writeChain = writeChain.then(async () => {
        let offset = 0;
        while (offset < bytes.length) {
          const { bytesWritten } = await handle.write(bytes, offset, bytes.length - offset);
          if (!bytesWritten) throw new Error("log write made no progress");
          offset += bytesWritten;
        }
      }).catch((error) => { logError ??= error; });
    };

    outcome = await new Promise((resolve) => {
      if (isCancelled?.()) { resolve({ cancelled: true }); return; }
      let child;
      try {
        child = spawnFn(check.argv[0], check.argv.slice(1), {
          cwd: resolvedCwd,
          stdio: ["ignore", "pipe", "pipe"],
          shell: false,
          windowsHide: true,
        });
      } catch (error) {
        resolve({
          error: error instanceof Error ? error.message : String(error),
          timed_out: false,
        });
        return;
      }
      let settled = false;
      let timedOut = false;
      let processError = null;
      const timer = setTimeout(() => {
        timedOut = true;
        (terminateFn ?? terminateChild)(child);
      }, check.timeout_ms);
      child.stdout?.on("data", append);
      child.stderr?.on("data", append);
      child.on("error", (error) => {
        if (settled) return;
        processError = error.message;
        append(`\n[spawn error] ${error.message}\n`);
        // ChildProcess emits close after error; retain the lock until close.
      });
      child.on("close", (code) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve({
          exit_code: typeof code === "number" ? code : null,
          timed_out: timedOut,
          cancelled: Boolean(isCancelled?.()),
          error: processError,
        });
      });
      onChild?.(child);
      if (isCancelled?.()) (terminateFn ?? terminateChild)(child);
    });
  } catch (error) {
    outcome.error = error instanceof Error ? error.message : String(error);
  } finally {
    if (handle) {
      await writeChain;
      try { await handle.close(); } catch (error) { logError ??= error; }
    }
  }
  return {
    ...result,
    exit_code: outcome.exit_code ?? null,
    timed_out: Boolean(outcome.timed_out),
    cancelled: Boolean(outcome.cancelled),
    error: logError ? `log I/O failed: ${logError.message}` : outcome.error ?? null,
    duration_ms: Date.now() - started,
  };
}
