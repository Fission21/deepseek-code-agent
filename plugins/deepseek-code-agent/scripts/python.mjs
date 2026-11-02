import { spawnSync } from "node:child_process";

export function pythonCandidates() {
  const list = [];
  list.push({ command: "python3", prefix: [] });
  list.push({ command: "python", prefix: [] });
  if (process.platform === "win32") list.push({ command: "py", prefix: ["-3"] });
  return list;
}

export function usablePython(candidate) {
  try {
    const probe = spawnSync(
      candidate.command,
      [...candidate.prefix, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"],
      { stdio: "ignore", windowsHide: true },
    );
    return probe.status === 0;
  } catch {
    return false;
  }
}

export function resolvePython() {
  if (process.env.DSH_PYTHON && process.env.DSH_PYTHON.trim()) {
    const explicit = { command: process.env.DSH_PYTHON, prefix: [] };
    return usablePython(explicit) ? explicit : null;
  }
  for (const candidate of pythonCandidates()) {
    if (usablePython(candidate)) return candidate;
  }
  return null;
}

export function pythonErrorMessage() {
  if (process.env.DSH_PYTHON && process.env.DSH_PYTHON.trim()) {
    return (
      `DSH_PYTHON is set to ${process.env.DSH_PYTHON}, but it is not a usable ` +
      "Python 3.9+ interpreter. Fix or unset it; no PATH fallback is used."
    );
  }
  return "DSH requires Python 3.9+ but no usable interpreter was found. Install Python or set DSH_PYTHON to its absolute path.";
}
