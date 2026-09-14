import { spawn } from "node:child_process";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

export const PROVIDER_ID = "opencode-go";
export const MODEL_ID = "deepseek-v4.1-flash";
export const VARIANT = "max";

export function resolveModelSelection({ provider = PROVIDER_ID, model, variant } = {}) {
  if (![PROVIDER_ID, "deepseek"].includes(provider)) {
    throw new Error("provider must be opencode-go or deepseek");
  }
  if (model === undefined) model = provider === "deepseek" ? "deepseek-flash" : MODEL_ID;
  if (typeof model !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(model)) {
    throw new Error("model must be a model ID without a provider prefix or whitespace");
  }
  if (variant === undefined) {
    variant = provider === PROVIDER_ID && model === MODEL_ID ? VARIANT : null;
  }
  if (variant !== null && (typeof variant !== "string" || !/^[A-Za-z0-9_-]+$/.test(variant))) {
    throw new Error("variant must be a non-empty variant name or null for the model default");
  }
  return { provider, model, variant };
}

const MODEL_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const VARIANT_PATTERN = /^[A-Za-z0-9_-]+$/;
const PROVIDER_DEFAULT_MODELS = { [PROVIDER_ID]: MODEL_ID, deepseek: "deepseek-flash" };

function modelDefaultVariant(provider, model) {
  return provider === PROVIDER_ID && model === MODEL_ID ? VARIANT : null;
}

export function resolveEffectiveSelection(options = {}, saved = null) {
  const { provider: explicitProvider, model: explicitModel, variant: explicitVariant } = options;
  if (explicitProvider !== undefined && ![PROVIDER_ID, "deepseek"].includes(explicitProvider)) {
    throw new Error("provider must be opencode-go or deepseek");
  }
  if (
    explicitModel !== undefined &&
    (typeof explicitModel !== "string" || !MODEL_PATTERN.test(explicitModel))
  ) {
    throw new Error("model must be a model ID without a provider prefix or whitespace");
  }
  if (
    explicitVariant !== undefined &&
    explicitVariant !== null &&
    (typeof explicitVariant !== "string" || !VARIANT_PATTERN.test(explicitVariant))
  ) {
    throw new Error("variant must be a non-empty variant name or null for the model default");
  }
  const baseProvider = saved?.provider ?? PROVIDER_ID;
  const baseModel = saved?.model ?? MODEL_ID;
  let provider;
  let model;
  if (explicitProvider !== undefined) {
    provider = explicitProvider;
    model = explicitModel ?? PROVIDER_DEFAULT_MODELS[provider];
  } else if (explicitModel !== undefined) {
    provider = baseProvider;
    model = explicitModel;
  } else {
    provider = baseProvider;
    model = baseModel;
  }
  let variant;
  if (explicitVariant !== undefined) {
    variant = explicitVariant;
  } else if (saved && provider === baseProvider && model === baseModel) {
    variant = saved.variant;
  } else {
    variant = modelDefaultVariant(provider, model);
  }
  return { provider, model, variant };
}

function selectionForState(state) {
  // Records written before model selection was introduced always used the original default.
  return resolveModelSelection(state.model_selection ?? {});
}

export const BUILTIN_SELECTION = { provider: PROVIDER_ID, model: MODEL_ID, variant: VARIANT };
const DEFAULTS_FILE = "defaults.json";
const DEFAULTS_VERSION = 1;

function selectionSummary(state) {
  const selection = selectionForState(state);
  return {
    provider: selection.provider,
    model: `${selection.provider}/${selection.model}`,
    variant: selection.variant,
  };
}

const SESSION_ID_PATTERN = /^ses[A-Za-z0-9_-]+$/;
const REQUEST_ID_PATTERN = /^(per|que)[A-Za-z0-9_-]+$/;
const WORKTREE_PREFIX = "deepseek-code-agent-";
const MAX_WAIT_MS = 55_000;
const POLL_MS = 600;
const MAX_PART_TEXT = 20_000;
const MAX_REPORT_CHARS = 3000;
const REPORT_TRUNCATION_MARKER =
  "\n[truncated; call ds_inspect_agent with detail=full to read the complete report]";
const CURSOR_REVISION_PATTERN = /^[0-9a-f]{8,64}$/i;
const INSTRUCTION_FILE_NAMES = ["AGENTS.override.md", "AGENTS.md"];

export const WORKER_SYSTEM_PROMPT = `You are the implementation worker supervised by Codex.
Before editing, read every file in the controller-provided instruction manifest and follow it. Do not scan unrelated policy documents unless the task or a listed instruction routes you to them.
Work only on the assigned objective and scope. Treat controller-provided critical constraints as non-negotiable, while the original repository instruction files remain authoritative.
Do not commit, push, merge, deploy, modify production data, expose credentials, or discard unrelated changes.
Own high-volume discovery, implementation, tests, routine fixes, documentation and evidence preparation within scope; do not narrate each step back to the controller. Make routine implementation decisions independently. Escalate blocking consequential choices with options, tradeoffs, your recommendation and evidence so the queen can decide.
Finish with a handoff of at most 1500 characters that states the outcome, changed files, the checks actually run with results, remaining risks, and the instruction manifest acknowledgement (path, sha256, read scope) for every listed file.`;

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function opencodeBinary() {
  if (process.env.OPENCODE_BIN) return process.env.OPENCODE_BIN;
  return process.platform === "win32" ? "opencode.cmd" : "opencode";
}

export function commandInvocation(
  command,
  args,
  platform = process.platform,
  environment = process.env,
) {
  if (platform === "win32" && /\.(?:cmd|bat)$/i.test(command)) {
    return {
      command: environment.ComSpec ?? environment.COMSPEC ?? "cmd.exe",
      args: ["/d", "/s", "/c", command.includes(" ") ? `"${command}"` : command, ...args],
    };
  }
  return { command, args };
}

function terminateChild(child) {
  if (!child || child.killed) return;
  if (process.platform === "win32" && child.pid) {
    const killer = spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    });
    killer.unref();
    return;
  }
  child.kill("SIGTERM");
}

function basicAuth(username, password) {
  return `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`;
}

function assertAgentID(agentID) {
  if (!SESSION_ID_PATTERN.test(agentID)) {
    throw new Error(`Invalid agent_id: ${agentID}`);
  }
}

function assertRequestID(requestID) {
  if (!REQUEST_ID_PATTERN.test(requestID)) {
    throw new Error(`Invalid request_id: ${requestID}`);
  }
}

function boundedInteger(value, fallback, minimum, maximum) {
  if (value === undefined || value === null) return fallback;
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(maximum, Math.max(minimum, Math.trunc(number)));
}

function normalizeRelativePath(value, field, { allowDot = false } = {}) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${field} entries must be non-empty strings`);
  }
  const portable = value.trim().replaceAll("\\", "/");
  if (path.posix.isAbsolute(portable) || /^[A-Za-z]:\//.test(portable)) {
    throw new Error(`${field} entries must be workspace-relative: ${value}`);
  }
  const normalized = path.posix.normalize(portable);
  if (
    normalized === ".." ||
    normalized.startsWith("../") ||
    (!allowDot && normalized === ".")
  ) {
    throw new Error(`${field} entries must stay inside the workspace: ${value}`);
  }
  return normalized;
}

function normalizeRequiredRead(value) {
  if (typeof value === "string") {
    return { path: normalizeRelativePath(value, "required_reads"), sections: [] };
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("required_reads entries must be paths or { path, sections } objects");
  }
  if (
    value.sections !== undefined &&
    (!Array.isArray(value.sections) ||
      value.sections.some((section) => typeof section !== "string" || !section.trim()))
  ) {
    throw new Error("required_reads sections must be non-empty strings");
  }
  return {
    path: normalizeRelativePath(value.path, "required_reads"),
    sections: [...new Set((value.sections ?? []).map((section) => section.trim()))],
  };
}

function resolveInside(root, relativePath, field) {
  const target = path.resolve(root, ...relativePath.split("/"));
  const relative = path.relative(root, target);
  if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error(`${field} entries must stay inside the workspace: ${relativePath}`);
  }
  return target;
}

async function optionalInstructionFile(directory) {
  for (const name of INSTRUCTION_FILE_NAMES) {
    const target = path.join(directory, name);
    try {
      if ((await fs.stat(target)).isFile()) return target;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  }
  return null;
}

async function instructionRecord(workspaceRoot, relativePath, source, sections = []) {
  const absolute = resolveInside(workspaceRoot, relativePath, "instruction paths");
  let stat;
  try {
    stat = await fs.stat(absolute);
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error(
        `Required instruction file is missing from the agent workspace: ${relativePath}. ` +
          "A worktree contains committed HEAD only; use workspace_mode=current when the task depends on uncommitted instructions.",
      );
    }
    throw error;
  }
  if (!stat.isFile()) throw new Error(`Instruction path is not a file: ${relativePath}`);

  const [realRoot, realTarget, content] = await Promise.all([
    fs.realpath(workspaceRoot),
    fs.realpath(absolute),
    fs.readFile(absolute),
  ]);
  const realRelative = path.relative(realRoot, realTarget);
  if (
    realRelative === ".." ||
    realRelative.startsWith(`..${path.sep}`) ||
    path.isAbsolute(realRelative)
  ) {
    throw new Error(`Instruction path resolves outside the workspace: ${relativePath}`);
  }
  return {
    path: relativePath,
    sha256: createHash("sha256").update(content).digest("hex"),
    bytes: content.byteLength,
    sources: [source],
    sections,
  };
}

export async function discoverInstructionManifest({
  workspaceRoot,
  scopePaths = [],
  requiredReads = [],
}) {
  const root = path.resolve(workspaceRoot);
  const normalizedScopes = (scopePaths.length ? scopePaths : ["."]).map((value) =>
    normalizeRelativePath(value, "scope_paths", { allowDot: true }),
  );
  const normalizedRequired = requiredReads.map(normalizeRequiredRead);
  const selected = new Map();

  const add = async (relativePath, source, sections = []) => {
    const normalized = relativePath.split(path.sep).join("/");
    const existing = selected.get(normalized);
    if (existing) {
      if (!existing.sources.includes(source)) existing.sources.push(source);
      if (existing.sections.length > 0) {
        if (sections.length === 0) existing.sections = [];
        else existing.sections = [...new Set([...existing.sections, ...sections])];
      }
      return;
    }
    selected.set(normalized, await instructionRecord(root, normalized, source, sections));
  };

  for (const scopePath of normalizedScopes) {
    const absoluteScope = resolveInside(root, scopePath, "scope_paths");
    let targetDirectory = absoluteScope;
    try {
      if (!(await fs.stat(absoluteScope)).isDirectory()) targetDirectory = path.dirname(absoluteScope);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      targetDirectory = path.dirname(absoluteScope);
    }

    const relativeDirectory = path.relative(root, targetDirectory);
    const segments = relativeDirectory && relativeDirectory !== "." ? relativeDirectory.split(path.sep) : [];
    for (let depth = 0; depth <= segments.length; depth += 1) {
      const directory = path.join(root, ...segments.slice(0, depth));
      const instruction = await optionalInstructionFile(directory);
      if (!instruction) continue;
      await add(path.relative(root, instruction), "applicable_agents");
    }
  }

  for (const required of normalizedRequired) {
    await add(required.path, "required_read", required.sections);
  }

  const files = [...selected.values()];
  return {
    files,
    total_bytes: files.reduce((total, file) => total + file.bytes, 0),
  };
}

function renderBulletList(values, emptyText) {
  return values.length ? values.map((value) => `- ${value}`).join("\n") : `- ${emptyText}`;
}

export function buildInitialTask({ task, scopePaths, criticalConstraints, instructionManifest }) {
  const manifestLines = instructionManifest.files.map(
    (file) =>
      `- ${file.path} | sha256=${file.sha256} | bytes=${file.bytes} | ` +
      (file.sections.length ? `sections=${JSON.stringify(file.sections)}` : "read=full"),
  );
  return `${task.trim()}

Controller instruction packet

Scope paths:
${renderBulletList(scopePaths, "No narrower path supplied; remain within the task's stated scope.")}

Critical constraints:
${renderBulletList(criticalConstraints, "No additional controller summary; repository instructions remain authoritative.")}

Required instruction manifest (${instructionManifest.total_bytes} bytes total):
${renderBulletList(manifestLines, "No repository instruction file was discovered or explicitly selected.")}

Before editing, read every listed instruction from this workspace. Read a full file only when its entry says read=full; otherwise locate and read the named sections with enough surrounding context to apply them correctly. Do not paste instruction contents into the response. In the final response, repeat each manifest path, sha256, and read scope so Codex can compare it with this controller-generated manifest.`;
}

export function parseServerURL(text) {
  const match = text.match(/https?:\/\/127\.0\.0\.1:\d+/);
  return match?.[0] ?? null;
}

export function latestMessageID(messages) {
  if (!Array.isArray(messages) || messages.length === 0) return null;
  return messages.at(-1)?.info?.id ?? null;
}

function compactPart(part) {
  if (!part || typeof part !== "object") return null;
  if (part.type === "text") {
    const text = part.text ?? "";
    return {
      type: "text",
      text:
        text.length <= MAX_PART_TEXT
          ? text
          : `${text.slice(0, MAX_PART_TEXT)}\n[truncated by DeepSeek bridge]`,
    };
  }
  if (part.type === "patch") {
    return { type: "patch", hash: part.hash, files: part.files ?? [] };
  }
  if (part.type === "step-finish") {
    return {
      type: "step-finish",
      reason: part.reason,
      cost: part.cost,
      tokens: part.tokens,
    };
  }
  if (part.type === "tool") {
    return {
      type: "tool",
      tool: part.tool,
      status: part.state?.status,
      title: part.state?.title,
    };
  }
  return null;
}

function errorFingerprint(error) {
  if (error === null || error === undefined) return null;
  if (typeof error === "string") return error;
  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
}

export function summarizeError(error) {
  if (error === null || error === undefined) return null;
  if (typeof error === "string") return { message: error };
  const name = typeof error.name === "string" && error.name ? error.name : null;
  let message = typeof error.message === "string" && error.message ? error.message : null;
  if (!message && error.data && typeof error.data.message === "string" && error.data.message) {
    message = error.data.message;
  }
  const summary = { message: message ?? "Assistant turn failed" };
  if (name) summary.name = name;
  return summary;
}

export function messageRevision(message) {
  const info = message?.info ?? {};
  const parts = Array.isArray(message?.parts) ? message.parts : [];
  const signature = {
    id: info.id ?? null,
    role: info.role ?? null,
    completed: info.time?.completed ?? null,
    finish: info.finish ?? null,
    error: errorFingerprint(info.error),
    parts: parts.map((part) => {
      if (!part || typeof part !== "object") return ["unknown"];
      if (part.type === "text") return ["text", part.text ?? ""];
      if (part.type === "tool") {
        return ["tool", part.tool ?? null, part.state?.status ?? null, part.state?.title ?? null];
      }
      if (part.type === "step-finish") {
        return [
          "step-finish",
          part.reason ?? null,
          part.cost ?? null,
          part.tokens ? JSON.stringify(part.tokens) : null,
        ];
      }
      if (part.type === "patch") {
        return ["patch", part.hash ?? null, JSON.stringify(part.files ?? [])];
      }
      return [part.type ?? "unknown"];
    }),
  };
  return createHash("sha256").update(JSON.stringify(signature)).digest("hex").slice(0, 24);
}

export function parseCursor(cursor) {
  if (cursor === null || cursor === undefined) return null;
  if (typeof cursor === "object") {
    const messageID = cursor.message_id ?? cursor.messageID ?? cursor.id;
    if (typeof messageID !== "string" || !messageID) return null;
    const revision =
      typeof cursor.revision === "string" && CURSOR_REVISION_PATTERN.test(cursor.revision)
        ? cursor.revision.toLowerCase()
        : null;
    return { message_id: messageID, revision };
  }
  if (typeof cursor !== "string" || !cursor) return null;
  const match = cursor.match(/^(.*)@([0-9a-f]{8,64})$/i);
  if (match && match[1]) return { message_id: match[1], revision: match[2].toLowerCase() };
  return { message_id: cursor, revision: null };
}

export function encodeCursor(cursor) {
  const parsed = parseCursor(cursor);
  if (!parsed) return null;
  return parsed.revision ? `${parsed.message_id}@${parsed.revision}` : parsed.message_id;
}

export function selectMessages(messages, cursor = null, limit = null) {
  if (!Array.isArray(messages)) {
    return { messages: [], selected: [], cursor: null, previous: null, changed: false };
  }
  const parsed = parseCursor(cursor);
  let selected = messages;
  if (parsed) {
    const index = messages.findIndex((message) => message?.info?.id === parsed.message_id);
    if (index >= 0) {
      if (parsed.revision === null) {
        // Legacy message-ID cursors have no revision; keep the historical behavior
        // of treating the cursor message as already consumed.
        selected = messages.slice(index + 1);
      } else {
        const revision = messageRevision(messages[index]);
        selected = revision === parsed.revision ? messages.slice(index + 1) : messages.slice(index);
      }
    }
  }
  const latest = messages.at(-1);
  const latestID = latest?.info?.id ?? null;
  return {
    messages: limit === null || limit === undefined ? selected : selected.slice(-limit),
    selected,
    cursor: latestID ? { message_id: latestID, revision: messageRevision(latest) } : null,
    previous: parsed,
    changed: selected.length > 0,
  };
}

function isToolCallsStep(message) {
  if (typeof message?.info?.finish === "string" && message.info.finish.toLowerCase().includes("tool")) return true;
  const parts = Array.isArray(message?.parts) ? message.parts : [];
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    const part = parts[index];
    if (part?.type !== "step-finish") continue;
    const reason = typeof part.reason === "string" ? part.reason.toLowerCase() : "";
    return reason.includes("tool");
  }
  return false;
}

export function turnOutcome(messages) {
  const last = Array.isArray(messages) ? messages.at(-1) : null;
  if (!last) return { status: "empty", error: null };
  const info = last.info ?? {};
  if (info.role !== "assistant") return { status: "awaiting_assistant", error: null };
  if (info.error) return { status: "failed", error: summarizeError(info.error) };
  if (info.time?.completed === undefined || info.time?.completed === null) {
    return { status: "streaming", error: null };
  }
  if (isToolCallsStep(last)) return { status: "tool_calls", error: null };
  return { status: "completed", error: null };
}

export function finalReportFrom(messages) {
  const last = Array.isArray(messages) ? messages.at(-1) : null;
  if (!last || last.info?.role !== "assistant") return null;
  if (last.info.error) return null;
  if (last.info.time?.completed === undefined || last.info.time?.completed === null) return null;
  if (isToolCallsStep(last)) return null;
  const text = (last.parts ?? [])
    .filter((part) => part?.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n")
    .trim();
  return text || null;
}

export function boundReport(text, limit = MAX_REPORT_CHARS) {
  if (typeof text !== "string" || !text.trim()) return { text: null, truncated: false };
  const trimmed = text.trim();
  if (trimmed.length <= limit) return { text: trimmed, truncated: false };
  const available = Math.max(0, limit - REPORT_TRUNCATION_MARKER.length);
  return { text: `${trimmed.slice(0, available)}${REPORT_TRUNCATION_MARKER}`, truncated: true };
}

function finiteNumberOrNull(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function emptyTokenFields() {
  return {
    input: null,
    output: null,
    reasoning: null,
    cache_read: null,
    cache_write: null,
    total: null,
  };
}

function readTokenFields(container) {
  if (!container || typeof container !== "object") return null;
  const cache = container.cache && typeof container.cache === "object" ? container.cache : {};
  const fields = {
    input: finiteNumberOrNull(container.input),
    output: finiteNumberOrNull(container.output),
    reasoning: finiteNumberOrNull(container.reasoning),
    cache_read: finiteNumberOrNull(cache.read ?? container.cache_read),
    cache_write: finiteNumberOrNull(cache.write ?? container.cache_write),
    total: finiteNumberOrNull(container.total),
  };
  return Object.values(fields).some((value) => value !== null) ? fields : null;
}

function addTokenFields(total, fields) {
  for (const key of Object.keys(total)) {
    if (fields[key] !== null && fields[key] !== undefined) {
      total[key] = (total[key] ?? 0) + fields[key];
    }
  }
}

function stepFinishUsage(parts) {
  let cost = null;
  let tokens = null;
  for (const part of parts ?? []) {
    if (part?.type !== "step-finish") continue;
    const partCost = finiteNumberOrNull(part.cost);
    if (partCost !== null) cost = (cost ?? 0) + partCost;
    const fields = readTokenFields(part.tokens);
    if (fields) {
      tokens ??= emptyTokenFields();
      addTokenFields(tokens, fields);
    }
  }
  return { cost, tokens };
}

export function aggregateUsage(messages) {
  const tokens = emptyTokenFields();
  let cost = 0;
  let costSeen = false;
  let tokensSeen = false;
  let fallbackUsed = false;
  let assistantMessages = 0;
  let messagesWithCost = 0;
  let messagesWithTokens = 0;
  const tokenFieldMessages = Object.fromEntries(Object.keys(tokens).map(key => [key, 0]));

  for (const message of Array.isArray(messages) ? messages : []) {
    if (message?.info?.role !== "assistant") continue;
    assistantMessages += 1;
    const info = message.info;
    let messageCost = finiteNumberOrNull(info.cost);
    let messageTokens = readTokenFields(info.tokens);
    if (messageCost === null || messageTokens === null) {
      // Step-finish parts repeat message-level totals, so they are only a fallback
      // when the message itself carries no usage. This avoids double counting.
      const fallback = stepFinishUsage(message.parts);
      if (messageCost === null && fallback.cost !== null) {
        messageCost = fallback.cost;
        fallbackUsed = true;
      }
      if (messageTokens === null && fallback.tokens !== null) {
        messageTokens = fallback.tokens;
        fallbackUsed = true;
      }
    }
    if (messageCost !== null) {
      cost += messageCost;
      costSeen = true;
      messagesWithCost += 1;
    }
    if (messageTokens !== null) {
      addTokenFields(tokens, messageTokens);
      for (const [key, value] of Object.entries(messageTokens)) {
        if (value !== null) tokenFieldMessages[key] += 1;
      }
      tokensSeen = true;
      messagesWithTokens += 1;
    }
  }

  const notes = [];
  if (assistantMessages === 0) notes.push("no assistant messages yet");
  if (messagesWithCost < assistantMessages) {
    notes.push(
      `cost unavailable for ${assistantMessages - messagesWithCost} of ${assistantMessages} assistant messages`,
    );
  }
  if (messagesWithTokens < assistantMessages) {
    notes.push(
      `token usage unavailable for ${assistantMessages - messagesWithTokens} of ${assistantMessages} assistant messages`,
    );
  }
  if (tokensSeen) {
    const missingFields = Object.entries(tokenFieldMessages)
      .filter(([, count]) => count < assistantMessages)
      .map(([key]) => key);
    if (missingFields.length) notes.push(`token fields unavailable or partial: ${missingFields.join(", ")}`);
  }

  return {
    source: fallbackUsed
      ? "assistant_message_info_plus_step_finish_fallback"
      : "assistant_message_info",
    assistant_messages: assistantMessages,
    messages_with_cost: messagesWithCost,
    messages_with_tokens: messagesWithTokens,
    token_field_messages: tokenFieldMessages,
    complete:
      assistantMessages > 0 &&
      messagesWithCost === assistantMessages &&
      messagesWithTokens === assistantMessages &&
      Object.values(tokenFieldMessages).every(count => count === assistantMessages) &&
      messages.every(message => message?.info?.role !== "assistant" || message.info.time?.completed != null),
    cost_available: costSeen,
    cost_usd: costSeen ? cost : null,
    tokens_available: tokensSeen,
    tokens: tokensSeen ? tokens : null,
    note: notes.length ? notes.join("; ") : null,
  };
}

function bridgeStateFor({ rawStatusType, pendingCount, queuedMessages, changed, outcome }) {
  if (pendingCount > 0) return "needs_attention";
  if (rawStatusType === "busy") return queuedMessages > 0 ? "queued" : "running";
  if (rawStatusType === "retry") return "retry";
  if (rawStatusType === undefined || rawStatusType === null || rawStatusType === "idle") {
    if (changed && outcome.status === "failed") return "failed";
    if (changed && outcome.status === "completed") return "completed";
    if (queuedMessages > 0) return "queued";
    return "idle";
  }
  return rawStatusType;
}

function messageSummary(message) {
  const info = message?.info ?? {};
  return {
    message_id: info.id,
    role: info.role,
    created_at: info.time?.created,
    completed_at: info.time?.completed,
    error: info.error ?? null,
    cost: info.cost,
    tokens: info.tokens,
    parts: (message?.parts ?? []).map(compactPart).filter(Boolean),
  };
}

export function compactMessages(messages, afterMessageID = null, limit = 20) {
  const selection = selectMessages(messages, afterMessageID, limit);
  return {
    messages: selection.messages.map(messageSummary),
    cursor: latestMessageID(messages),
  };
}

function payloadCursor(data) {
  const surfaced = data.bridgeState === "completed" || data.bridgeState === "failed";
  const holdsFinal = !surfaced && (data.outcome.status === "completed" || data.outcome.status === "failed");
  return encodeCursor(holdsFinal ? data.selection.previous : data.selection.cursor);
}

function compactPayload(data) {
  const report =
    data.bridgeState === "completed"
      ? boundReport(finalReportFrom(data.messages))
      : { text: null, truncated: false };
  const payload = {
    agent_id: data.agentID,
    ...selectionSummary(data.state),
    detail: "compact",
    state: data.bridgeState,
    cursor: payloadCursor(data),
    queued_messages: data.queuedMessages,
    pending: data.pending,
    final_report: report.text,
    report_truncated: report.truncated,
  };
  if (data.bridgeState === "completed" || data.bridgeState === "failed") {
    payload.usage = aggregateUsage(data.messages);
    payload.worktree = data.state.directory;
    payload.workspace_mode = data.state.workspace_mode;
  }
  if (report.truncated) {
    payload.report_hint =
      "Call ds_inspect_agent with detail=full (omit the cursor) to read the complete report.";
  }
  if (data.bridgeState === "failed" && data.outcome.error) payload.error = data.outcome.error;
  return payload;
}

function fullPayload(data, messageLimit = 20) {
  const limit = boundedInteger(messageLimit, 20, 1, 100);
  return {
    agent_id: data.agentID,
    ...selectionSummary(data.state),
    detail: "full",
    state: data.bridgeState,
    opencode_status: data.rawStatus,
    cursor: payloadCursor(data),
    messages: data.selection.messages.slice(-limit).map(messageSummary),
    queued_messages: data.queuedMessages,
    pending: data.pending,
    usage: aggregateUsage(data.messages),
    final_report: data.bridgeState === "completed" ? finalReportFrom(data.messages) : null,
    worktree: data.state.directory,
    workspace_mode: data.state.workspace_mode,
    scope_paths: data.state.scope_paths ?? [],
    instruction_manifest: data.state.instruction_manifest ?? { files: [], total_bytes: 0 },
  };
}

function cursorBeforeAssistantMessages(messages) {
  if (!Array.isArray(messages)) return null;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const info = messages[index]?.info;
    if (!info?.id || info.role === "assistant") continue;
    return { message_id: info.id, revision: messageRevision(messages[index]) };
  }
  return null;
}

async function runCommand(command, args, options = {}) {
  const timeoutMs = options.timeoutMs ?? 15_000;
  return await new Promise((resolve, reject) => {
    const invocation = commandInvocation(command, args);
    const child = spawn(invocation.command, invocation.args, {
      cwd: options.cwd,
      env: options.env ?? process.env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      terminateChild(child);
      reject(new Error(`${command} timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ code, stdout, stderr });
    });
  });
}

async function discardPreparedWorktree(prepared) {
  if (prepared.workspaceMode !== "worktree" || !prepared.tempRoot) return;
  const remove = await runCommand(
    "git",
    ["-C", prepared.sourceRoot, "worktree", "remove", "--force", prepared.directory],
    { timeoutMs: 60_000 },
  );
  if (remove.code !== 0) {
    throw new Error(`Unable to clean up rejected worktree: ${remove.stderr || remove.stdout}`);
  }
  await fs.rm(prepared.tempRoot, { recursive: true, force: true });
}

export class DeepSeekController {
  constructor(options = {}) {
    this.fetch = options.fetch ?? globalThis.fetch;
    this.spawn = options.spawn ?? spawn;
    this.runCommand = options.runCommand ?? runCommand;
    this.stateRoot = path.resolve(
      options.stateRoot ??
        process.env.DEEPSEEK_AGENT_STATE_DIR ??
        path.join(os.homedir(), ".deepseek-code-agent"),
    );
    this.server = null;
    this.serverPromise = null;
  }

  defaultsPath() {
    return path.join(this.stateRoot, DEFAULTS_FILE);
  }

  async readDefaults() {
    let text;
    try {
      text = await fs.readFile(this.defaultsPath(), "utf8");
    } catch (error) {
      if (error?.code === "ENOENT") return null;
      throw new Error(`Unable to read the saved model defaults (${DEFAULTS_FILE}): ${error.message}`);
    }
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (error) {
      throw new Error(
        `Saved model defaults (${DEFAULTS_FILE}) are malformed and must be fixed or reset: ${error.message}`,
      );
    }
    if (
      !parsed ||
      typeof parsed !== "object" ||
      Array.isArray(parsed) ||
      parsed.version !== DEFAULTS_VERSION
    ) {
      throw new Error(
        `Saved model defaults (${DEFAULTS_FILE}) have an unsupported format (version ${
          parsed?.version ?? "missing"
        }); run ds_model_defaults with action=reset to restore the built-in default.`,
      );
    }
    const missing = ["provider", "model", "variant"].filter((field) => !Object.hasOwn(parsed, field));
    if (missing.length) {
      throw new Error(
        `Saved model defaults (${DEFAULTS_FILE}) are incomplete (missing ${missing.join(", ")}); run ds_model_defaults with action=reset.`,
      );
    }
    const { provider, model, variant } = parsed;
    try {
      return resolveModelSelection({ provider, model, variant });
    } catch (error) {
      throw new Error(`Saved model defaults (${DEFAULTS_FILE}) are invalid: ${error.message}; run ds_model_defaults with action=reset.`);
    }
  }

  async writeDefaults(selection) {
    const target = this.defaultsPath();
    await fs.mkdir(this.stateRoot, { recursive: true, mode: 0o700 });
    const payload = JSON.stringify({ version: DEFAULTS_VERSION, ...selection }, null, 2);
    const temporary = `${target}.${process.pid}.${randomUUID()}.tmp`;
    await fs.writeFile(temporary, `${payload}\n`, { mode: 0o600 });
    try {
      await fs.rename(temporary, target);
    } catch (error) {
      await fs.rm(temporary, { force: true }).catch(() => {});
      throw error;
    }
    return { ...selection };
  }

  async clearDefaults() {
    try {
      await fs.unlink(this.defaultsPath());
      return true;
    } catch (error) {
      if (error?.code === "ENOENT") return false;
      throw error;
    }
  }

  async modelDefaults({ action = "get", provider, model, variant, workspace } = {}) {
    if (!new Set(["get", "set", "reset"]).has(action)) {
      throw new Error("action must be get, set, or reset");
    }
    if (action === "reset") {
      await this.clearDefaults();
      return {
        action,
        ...BUILTIN_SELECTION,
        configured: "built-in",
        source: "built_in",
        settings_path: this.defaultsPath(),
      };
    }
    if (action === "set") {
      if (provider === undefined && model === undefined && variant === undefined) {
        throw new Error("set requires at least one of provider, model, or variant");
      }
      const current = await this.readDefaults();
      // Validate against the catalog before anything is saved; a failure leaves
      // the previous setting untouched.
      const selection = resolveEffectiveSelection({ provider, model, variant }, current);
      await this.requireModel(
        selection,
        workspace === undefined ? undefined : await fs.realpath(workspace),
      );
      await this.writeDefaults(selection);
      return {
        action,
        ...selection,
        configured: { ...selection },
        source: "settings_file",
        credentials_verified: false,
        inference_verified: false,
        settings_path: this.defaultsPath(),
      };
    }
    const saved = await this.readDefaults();
    const selection = resolveEffectiveSelection({}, saved);
    return {
      action,
      ...selection,
      configured: saved
        ? { provider: saved.provider, model: saved.model, variant: saved.variant }
        : "built-in",
      source: saved ? "settings_file" : "built_in",
      settings_path: this.defaultsPath(),
    };
  }

  async resolveSelection({ provider, model, variant } = {}) {
    const needsSaved =
      provider === undefined || model === undefined || variant === undefined;
    const saved = needsSaved ? await this.readDefaults() : null;
    return resolveEffectiveSelection({ provider, model, variant }, saved);
  }

  async modelAvailability(selection, directory) {
    const catalog = await this.request("GET", "/provider", { directory, timeoutMs: 15000 });
    if (!Array.isArray(catalog?.all) || !Array.isArray(catalog?.connected)) {
      throw new Error("OpenCode returned an unsupported provider catalog; update OpenCode and retry");
    }
    const provider = catalog.all.find((entry) => entry.id === selection.provider);
    const model = Object.hasOwn(provider?.models ?? {}, selection.model)
      ? provider.models[selection.model]
      : undefined;
    const variants = Object.entries(model?.variants ?? {})
      .filter(([, settings]) => !settings?.disabled)
      .map(([name]) => name);
    const connected = catalog.connected.includes(selection.provider);
    const variantAvailable = selection.variant === null || variants.includes(selection.variant);
    const ok = connected && Boolean(model) && variantAvailable;
    let hint = "Provider configured and model listed; credentials, balance and inference are not verified.";
    if (!model) {
      hint = `Model ${selection.provider}/${selection.model} is not in the OpenCode catalog. Refresh models or configure this model in OpenCode.`;
    } else if (!connected) {
      hint = `Connect ${selection.provider} in OpenCode using /connect or opencode auth login${selection.provider === "deepseek" ? ", or pass DEEPSEEK_API_KEY to the host process" : ""}.`;
    } else if (!variantAvailable) {
      hint = `Variant ${selection.variant} is unavailable. Available variants: ${variants.join(", ") || "none"}; use variant=null for the model default.`;
    }
    return {
      ok,
      provider_connected: connected,
      model_available: Boolean(model),
      variant_available: variantAvailable,
      available_variants: variants,
      credentials_verified: false,
      inference_verified: false,
      hint,
    };
  }

  async requireModel(selection, directory) {
    const availability = await this.modelAvailability(selection, directory);
    if (!availability.ok) throw new Error(availability.hint);
  }

  async check({ workspace, ...options } = {}) {
    const selection = await this.resolveSelection(options);
    const directory = workspace === undefined ? undefined : await fs.realpath(workspace);
    const [version, git] = await Promise.all([
      this.runCommand(opencodeBinary(), ["--version"]),
      this.runCommand("git", ["--version"]).catch((error) => ({
        code: -1,
        stdout: "",
        stderr: error instanceof Error ? error.message : String(error),
      })),
    ]);
    if (version.code !== 0) {
      throw new Error(`OpenCode is unavailable: ${version.stderr || version.stdout}`);
    }
    const availability = await this.modelAvailability(selection, directory);
    return {
      ...availability,
      ok: git.code === 0 && availability.ok,
      platform: process.platform,
      node_version: process.version,
      git_available: git.code === 0,
      git_version: git.stdout.trim(),
      opencode_version: version.stdout.trim(),
      ...selection,
      hint: git.code === 0 ? availability.hint : "Install Git before delegating work.",
    };
  }

  async ensureServer() {
    if (this.server) return this.server;
    if (this.serverPromise) return await this.serverPromise;
    this.serverPromise = new Promise((resolve, reject) => {
      const password = randomBytes(24).toString("hex");
      const invocation = commandInvocation(opencodeBinary(), [
        "serve",
        "--hostname",
        "127.0.0.1",
        "--port",
        "0",
      ]);
      const child = this.spawn(
        invocation.command,
        invocation.args,
        {
          env: { ...process.env, OPENCODE_SERVER_PASSWORD: password },
          stdio: ["ignore", "pipe", "pipe"],
          windowsHide: true,
        },
      );
      let buffer = "";
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        terminateChild(child);
        reject(new Error("Timed out starting the local OpenCode server"));
      }, 15_000);
      const onData = (chunk) => {
        if (settled) return;
        const text = chunk.toString();
        buffer += text;
        const url = parseServerURL(buffer);
        if (!url) return;
        settled = true;
        clearTimeout(timer);
        this.server = {
          url,
          child,
          authorization: basicAuth("opencode", password),
        };
        resolve(this.server);
      };
      child.stdout.on("data", onData);
      child.stderr.on("data", onData);
      child.on("error", (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(error);
      });
      child.on("exit", (code) => {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          reject(new Error(`OpenCode server exited before startup (code ${code})`));
        }
        if (this.server?.child === child) this.server = null;
      });
    }).finally(() => {
      this.serverPromise = null;
    });
    return await this.serverPromise;
  }

  async request(method, endpoint, { directory, body, timeoutMs } = {}) {
    const server = await this.ensureServer();
    const url = new URL(endpoint, server.url);
    if (directory) url.searchParams.set("directory", directory);
    const response = await this.fetch(url, {
      method,
      headers: {
        authorization: server.authorization,
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      ...(timeoutMs ? { signal: AbortSignal.timeout(timeoutMs) } : {}),
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`${method} ${url.pathname} failed (${response.status}): ${text}`);
    }
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }

  async prepareDirectory(workspace, workspaceMode) {
    const resolved = path.resolve(workspace);
    const stat = await fs.stat(resolved);
    if (!stat.isDirectory()) throw new Error(`Workspace is not a directory: ${resolved}`);
    if (workspaceMode === "current") {
      return {
        directory: resolved,
        sourceRoot: resolved,
        workspaceMode,
        tempRoot: null,
      };
    }
    const rootResult = await runCommand("git", ["-C", resolved, "rev-parse", "--show-toplevel"]);
    if (rootResult.code !== 0) {
      throw new Error("workspace_mode=worktree requires a Git repository");
    }
    const sourceRoot = rootResult.stdout.trim();
    const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), WORKTREE_PREFIX));
    const directory = path.join(tempRoot, "worktree");
    const add = await runCommand(
      "git",
      ["-C", sourceRoot, "worktree", "add", "--detach", directory, "HEAD"],
      { timeoutMs: 60_000 },
    );
    if (add.code !== 0) {
      await fs.rm(tempRoot, { recursive: true, force: true });
      throw new Error(`Unable to create worktree: ${add.stderr || add.stdout}`);
    }
    return { directory, sourceRoot, workspaceMode, tempRoot };
  }

  async stateDirectory() {
    const directory = path.join(this.stateRoot, "agents");
    await fs.mkdir(directory, { recursive: true, mode: 0o700 });
    return directory;
  }

  async queueDirectory(agentID) {
    assertAgentID(agentID);
    const directory = path.join(this.stateRoot, "queue", agentID);
    await fs.mkdir(directory, { recursive: true, mode: 0o700 });
    return directory;
  }

  async writeState(state) {
    assertAgentID(state.agent_id);
    const directory = await this.stateDirectory();
    const target = path.join(directory, `${state.agent_id}.json`);
    const temporary = `${target}.${process.pid}.${randomUUID()}.tmp`;
    await fs.writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, { mode: 0o600 });
    await fs.rename(temporary, target);
  }

  async readState(agentID) {
    assertAgentID(agentID);
    const target = path.join(await this.stateDirectory(), `${agentID}.json`);
    try {
      return JSON.parse(await fs.readFile(target, "utf8"));
    } catch (error) {
      if (error?.code === "ENOENT") {
        throw new Error(`Unknown agent_id: ${agentID}. Use ds_list_agents to recover local IDs.`);
      }
      throw error;
    }
  }

  async messages(state) {
    return await this.request("GET", `/session/${state.agent_id}/message`, {
      directory: state.directory,
      body: undefined,
    }).then((messages) => (Array.isArray(messages) ? messages : []));
  }

  async rawStatus(state) {
    const statuses = await this.request("GET", "/session/status", {
      directory: state.directory,
    });
    const status = statuses?.[state.agent_id];
    if (!status || typeof status !== "object") return { type: "idle" };
    return { ...status, type: status.type ?? "idle" };
  }

  async pending(state) {
    const safeList = async (endpoint) => {
      try {
        const value = await this.request("GET", endpoint, { directory: state.directory });
        return Array.isArray(value) ? value : [];
      } catch {
        return [];
      }
    };
    const [permissions, questions] = await Promise.all([
      safeList(`/api/session/${state.agent_id}/permission`),
      safeList(`/api/session/${state.agent_id}/question`),
    ]);
    return { permissions, questions };
  }

  async queueCount(agentID) {
    const directory = await this.queueDirectory(agentID);
    return (await fs.readdir(directory)).filter((name) => name.endsWith(".json")).length;
  }

  async enqueue(agentID, text) {
    const directory = await this.queueDirectory(agentID);
    const target = path.join(
      directory,
      `${Date.now().toString().padStart(16, "0")}-${randomUUID()}.json`,
    );
    await fs.writeFile(target, `${JSON.stringify({ text, queued_at: Date.now() })}\n`, {
      mode: 0o600,
    });
    return target;
  }

  async flushOne(state) {
    const directory = await this.queueDirectory(state.agent_id);
    const entries = (await fs.readdir(directory))
      .filter((name) => name.endsWith(".json"))
      .sort();
    if (entries.length === 0) return false;
    const target = path.join(directory, entries[0]);
    const queued = JSON.parse(await fs.readFile(target, "utf8"));
    await this.submit(state, queued.text);
    await fs.unlink(target);
    return true;
  }

  async submit(state, text) {
    const selection = selectionForState(state);
    await this.request("POST", `/session/${state.agent_id}/prompt_async`, {
      directory: state.directory,
      body: {
        model: { providerID: selection.provider, modelID: selection.model },
        agent: "build",
        ...(selection.variant === null ? {} : { variant: selection.variant }),
        system: WORKER_SYSTEM_PROMPT,
        parts: [{ type: "text", text }],
      },
    });
  }

  async spawnAgent({
    task,
    workspace,
    workspace_mode = "worktree",
    title,
    scope_paths = [],
    required_reads = [],
    critical_constraints = [],
    provider,
    model,
    variant,
  }) {
    if (typeof task !== "string" || !task.trim()) throw new Error("task is required");
    if (typeof workspace !== "string" || !workspace.trim()) {
      throw new Error("workspace is required");
    }
    if (!new Set(["worktree", "current"]).has(workspace_mode)) {
      throw new Error("workspace_mode must be worktree or current");
    }
    for (const [field, value] of Object.entries({ scope_paths, critical_constraints })) {
      if (!Array.isArray(value) || value.some((entry) => typeof entry !== "string")) {
        throw new Error(`${field} must be an array of strings`);
      }
    }
    if (!Array.isArray(required_reads)) {
      throw new Error("required_reads must be an array");
    }
    const selection = await this.resolveSelection({ provider, model, variant });
    const sourceDirectory = await fs.realpath(workspace);
    await this.requireModel(selection, sourceDirectory);
    const prepared = await this.prepareDirectory(sourceDirectory, workspace_mode);
    let normalizedScopePaths;
    let instructionManifest;
    let normalizedConstraints;
    let initialTask;
    try {
      // A worktree can have different committed provider settings from the source checkout.
      if (prepared.directory !== sourceDirectory) await this.requireModel(selection, prepared.directory);
      normalizedScopePaths = scope_paths.map((value) =>
        normalizeRelativePath(value, "scope_paths", { allowDot: true }),
      );
      instructionManifest = await discoverInstructionManifest({
        workspaceRoot: prepared.directory,
        scopePaths: normalizedScopePaths,
        requiredReads: required_reads,
      });
      normalizedConstraints = critical_constraints.map((value) => value.trim()).filter(Boolean);
      initialTask = buildInitialTask({
        task,
        scopePaths: normalizedScopePaths,
        criticalConstraints: normalizedConstraints,
        instructionManifest,
      });
    } catch (error) {
      try {
        await discardPreparedWorktree(prepared);
      } catch (cleanupError) {
        throw new AggregateError([error, cleanupError], "Instruction preflight and cleanup failed");
      }
      throw error;
    }
    const session = await this.request("POST", "/session", {
      directory: prepared.directory,
      body: {
        title: title?.trim() || `${selection.model}: ${task.trim().slice(0, 72)}`,
        agent: "build",
        model: { id: selection.model, providerID: selection.provider,
          ...(selection.variant === null ? {} : { variant: selection.variant }) },
        metadata: {
          controller: "deepseek-code-agent",
          source_root: prepared.sourceRoot,
          workspace_mode,
        },
      },
    });
    const state = {
      agent_id: session.id,
      status: "active",
      directory: prepared.directory,
      source_root: prepared.sourceRoot,
      workspace_mode,
      temp_root: prepared.tempRoot,
      owns_worktree: workspace_mode === "worktree",
      created_at: Date.now(),
      title: session.title,
      scope_paths: normalizedScopePaths,
      instruction_manifest: instructionManifest,
      model_selection: selection,
    };
    await this.writeState(state);
    await this.submit(state, initialTask);
    const messages = await this.messages(state);
    return {
      agent_id: state.agent_id,
      state: "running",
      cursor: encodeCursor(cursorBeforeAssistantMessages(messages)),
      workspace_mode,
      worktree: state.directory,
      source_root: state.source_root,
      scope_paths: state.scope_paths,
      instruction_manifest: state.instruction_manifest,
      ...selectionSummary(state),
    };
  }

  async collect(agentID, cursor) {
    const state = await this.readState(agentID);
    const [rawStatus, pending, messages, queuedMessages] = await Promise.all([
      this.rawStatus(state),
      this.pending(state),
      this.messages(state),
      this.queueCount(agentID),
    ]);
    const selection = selectMessages(messages, cursor, null);
    const outcome = turnOutcome(messages);
    const pendingCount = pending.permissions.length + pending.questions.length;
    const bridgeState = bridgeStateFor({
      rawStatusType: rawStatus?.type ?? "idle",
      pendingCount,
      queuedMessages,
      changed: selection.changed,
      outcome,
    });
    return {
      agentID,
      state,
      rawStatus,
      pending,
      messages,
      queuedMessages,
      selection,
      outcome,
      bridgeState,
    };
  }

  renderPayload(data, detail, messageLimit) {
    return detail === "full" ? fullPayload(data, messageLimit) : compactPayload(data);
  }

  async snapshot(agentID, afterMessageID = null, messageLimit = 20) {
    const data = await this.collect(agentID, afterMessageID);
    return fullPayload(data, messageLimit);
  }

  async sendMessage({ agent_id, message, queue_if_busy = true }) {
    if (typeof message !== "string" || !message.trim()) throw new Error("message is required");
    const state = await this.readState(agent_id);
    const status = await this.rawStatus(state);
    if (status.type !== "idle") {
      if (!queue_if_busy) {
        throw new Error(`Agent is ${status.type}; set queue_if_busy=true to use its mailbox`);
      }
      await this.enqueue(agent_id, message.trim());
      return {
        agent_id,
        state: "queued",
        queued_messages: await this.queueCount(agent_id),
      };
    }
    await this.submit(state, message.trim());
    return { agent_id, state: "running", queued_messages: await this.queueCount(agent_id) };
  }

  async waitAgent({ agent_id, cursor = null, timeout_ms = 30_000, detail = "compact" }) {
    const timeout = boundedInteger(timeout_ms, 30_000, 0, MAX_WAIT_MS);
    const deadline = Date.now() + timeout;
    const mode = detail === "full" ? "full" : "compact";
    while (true) {
      const data = await this.collect(agent_id, cursor);
      if (data.bridgeState === "needs_attention") {
        return this.renderPayload(data, mode);
      }
      if (data.rawStatus?.type === "idle" && data.queuedMessages > 0) {
        await this.flushOne(data.state);
      } else if (
        data.bridgeState === "completed" ||
        data.bridgeState === "retry" ||
        data.bridgeState === "failed"
      ) {
        return this.renderPayload(data, mode);
      }
      if (Date.now() >= deadline) {
        const final = await this.collect(agent_id, cursor);
        const terminal = new Set(["completed", "retry", "failed", "needs_attention"]);
        if (terminal.has(final.bridgeState)) return this.renderPayload(final, mode);
        return { ...this.renderPayload(final, mode), state: "timed_out" };
      }
      await delay(Math.min(POLL_MS, Math.max(1, deadline - Date.now())));
    }
  }

  async inspectAgent({
    agent_id,
    cursor = null,
    message_limit = 20,
    include_diff = false,
    detail = "compact",
  }) {
    const data = await this.collect(agent_id, cursor);
    const mode = detail === "full" ? "full" : "compact";
    const result = this.renderPayload(data, mode, boundedInteger(message_limit, 20, 1, 100));
    if (include_diff) {
      result.diff = await this.request("GET", `/session/${agent_id}/diff`, {
        directory: data.state.directory,
      });
    }
    return result;
  }

  async forkAgent({ agent_id, message_id = null }) {
    const state = await this.readState(agent_id);
    const status = await this.rawStatus(state);
    if (status.type !== "idle") throw new Error("Interrupt or wait for the parent before forking");
    const forked = await this.request("POST", `/session/${agent_id}/fork`, {
      directory: state.directory,
      body: message_id ? { messageID: message_id } : {},
    });
    const child = {
      ...state,
      agent_id: forked.id,
      status: "active",
      parent_agent_id: agent_id,
      owns_worktree: false,
      created_at: Date.now(),
      title: forked.title,
    };
    await this.writeState(child);
    return {
      agent_id: child.agent_id,
      parent_agent_id: agent_id,
      state: "completed",
      cursor: encodeCursor(selectMessages(await this.messages(child), null).cursor),
      worktree: child.directory,
      ...selectionSummary(child),
      warning: "The fork shares the parent's filesystem. Run parent and child sequentially.",
    };
  }

  async interruptAgent({ agent_id }) {
    const state = await this.readState(agent_id);
    await this.request("POST", `/session/${agent_id}/abort`, {
      directory: state.directory,
      body: {},
    });
    return { agent_id, state: "interrupted" };
  }

  async replyAgent({ agent_id, kind, request_id, reply, answers, message }) {
    const state = await this.readState(agent_id);
    assertRequestID(request_id);
    if (kind === "permission") {
      if (!new Set(["once", "always", "reject"]).has(reply)) {
        throw new Error("permission reply must be once, always, or reject");
      }
      await this.request(
        "POST",
        `/api/session/${agent_id}/permission/${request_id}/reply`,
        {
          directory: state.directory,
          body: { reply, ...(message ? { message } : {}) },
        },
      );
      return { agent_id, request_id, kind, reply };
    }
    if (kind === "question") {
      if (reply === "reject") {
        await this.request(
          "POST",
          `/api/session/${agent_id}/question/${request_id}/reject`,
          { directory: state.directory, body: {} },
        );
        return { agent_id, request_id, kind, reply: "reject" };
      }
      if (!Array.isArray(answers) || answers.some((answer) => !Array.isArray(answer))) {
        throw new Error("question answers must be an array of string arrays");
      }
      await this.request("POST", `/api/session/${agent_id}/question/${request_id}/reply`, {
        directory: state.directory,
        body: { answers },
      });
      return { agent_id, request_id, kind, reply: "answered" };
    }
    throw new Error("kind must be permission or question");
  }

  async listAgents() {
    const directory = await this.stateDirectory();
    const files = (await fs.readdir(directory)).filter((name) => name.endsWith(".json"));
    const states = [];
    for (const file of files) {
      try {
        states.push(JSON.parse(await fs.readFile(path.join(directory, file), "utf8")));
      } catch {
        // A partially copied state file is ignored; atomic writes prevent this normally.
      }
    }
    return {
      agents: states
        .sort((a, b) => b.created_at - a.created_at)
        .map((state) => ({
          agent_id: state.agent_id,
          status: state.status,
          title: state.title,
          worktree: state.directory,
          workspace_mode: state.workspace_mode,
          created_at: state.created_at,
          parent_agent_id: state.parent_agent_id,
          scope_paths: state.scope_paths ?? [],
          instruction_total_bytes: state.instruction_manifest?.total_bytes ?? 0,
          ...selectionSummary(state),
        })),
    };
  }

  async closeAgent({ agent_id, remove_worktree = false }) {
    const state = await this.readState(agent_id);
    try {
      await this.request("POST", `/session/${agent_id}/abort`, {
        directory: state.directory,
        body: {},
      });
    } catch {
      // The session may already be idle or its server may have restarted.
    }
    let removedWorktree = false;
    if (remove_worktree) {
      if (state.workspace_mode !== "worktree" || !state.temp_root) {
        throw new Error("Refusing to remove a workspace not created by this bridge");
      }
      if (state.owns_worktree !== true) {
        throw new Error(
          "Refusing to remove a worktree owned by another agent; close this fork without removal",
        );
      }
      const tempRoot = path.resolve(state.temp_root);
      const worktree = path.resolve(state.directory);
      if (
        !path.basename(tempRoot).startsWith(WORKTREE_PREFIX) ||
        path.dirname(worktree) !== tempRoot ||
        path.basename(worktree) !== "worktree"
      ) {
        throw new Error("Refusing to remove an unverified worktree path");
      }
      const remove = await runCommand(
        "git",
        ["-C", state.source_root, "worktree", "remove", "--force", worktree],
        { timeoutMs: 60_000 },
      );
      if (remove.code !== 0) {
        throw new Error(`Unable to remove worktree: ${remove.stderr || remove.stdout}`);
      }
      await fs.rm(tempRoot, { recursive: true, force: true });
      removedWorktree = true;
    }
    state.status = "closed";
    state.closed_at = Date.now();
    state.worktree_removed = removedWorktree;
    await this.writeState(state);
    return { agent_id, state: "closed", worktree_removed: removedWorktree };
  }

  shutdown() {
    if (this.server?.child && !this.server.child.killed) {
      terminateChild(this.server.child);
    }
    this.server = null;
  }
}
