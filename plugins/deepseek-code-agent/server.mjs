#!/usr/bin/env node
import readline from "node:readline";
import { pathToFileURL } from "node:url";

import { DeepSeekController } from "./src/controller.mjs";

const controller = new DeepSeekController();

const agentID = { type: "string", pattern: "^ses[A-Za-z0-9_-]+$" };
const modelOptions = {
  provider: {
    type: "string", enum: ["opencode-go", "deepseek"],
    description: "opencode-go uses the Go subscription; deepseek uses separately configured official API credentials. Omitted resolves from the saved machine default, else the built-in Go default.",
  },
  model: {
    type: "string", pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    description: "Model ID without provider prefix. Omitted resolves from the saved machine default, else the selected provider's Flash model. Must be listed in the local OpenCode catalog.",
  },
  variant: {
    type: ["string", "null"], pattern: "^[A-Za-z0-9_-]+$",
    description: "Reasoning variant supported by the selected model. Omitted resolves from the saved machine default when the provider/model pair matches it, else the runtime default (max only for Go deepseek-v4.1-flash). Explicit null uses the runtime default for any model.",
  },
};
const modelDefaultsSchema = {
  type: "object",
  properties: {
    action: {
      type: "string", enum: ["get", "set", "reset"], default: "get",
      description: "get returns the effective selection and its source. set validates the selection against the local OpenCode catalog, then persists it as the machine-wide default for new workers. reset removes the saved default and restores the built-in Go DeepSeek max selection.",
    },
    ...modelOptions,
    workspace: {
      type: "string", minLength: 1,
      description: "Optional workspace path used to validate a set action against project-specific OpenCode provider settings.",
    },
  },
  additionalProperties: false,
};
const cursor = {
  type: ["string", "null"],
  description:
    "Opaque revision cursor returned by a previous call. Legacy message-ID strings are accepted.",
};
const detail = {
  type: "string",
  enum: ["compact", "full"],
  default: "compact",
  description:
    "compact (default) returns status, a revision cursor, pending attention, a bounded final assistant report, and cumulative usage on completion/failure. full returns the legacy message-level snapshot including the instruction manifest and complete report.",
};

export const tools = [
  {
    name: "ds_check",
    description:
      "Check OpenCode and the selected Go or official DeepSeek model, provider configuration, and variants. Does not call the model or verify credentials, balance, or inference.",
    inputSchema: { type: "object", properties: {
      ...modelOptions,
      workspace: { type: "string", minLength: 1, description: "Optional workspace path for project-specific OpenCode provider settings." },
    }, additionalProperties: false },
  },
  {
    name: "ds_spawn_agent",
    description:
      "Create a persistent coding worker using OpenCode Go (DeepSeek or GLM) or the official DeepSeek API. Save the selected model for all follow-ups and forks. Codex owns review and verification.",
    inputSchema: {
      type: "object",
      properties: {
        ...modelOptions,
        task: { type: "string", minLength: 1 },
        workspace: { type: "string", minLength: 1 },
        workspace_mode: {
          type: "string",
          enum: ["worktree", "current"],
          default: "worktree",
          description:
            "worktree starts from Git HEAD in isolation; current edits the supplied working tree and sees uncommitted files.",
        },
        title: { type: "string" },
        scope_paths: {
          type: "array",
          items: { type: "string", minLength: 1 },
          maxItems: 100,
          description:
            "Workspace-relative files or directories that bound the implementation. Used to discover the applicable root-to-target AGENTS.md chain.",
        },
        required_reads: {
          type: "array",
          items: {
            oneOf: [
              { type: "string", minLength: 1 },
              {
                type: "object",
                properties: {
                  path: { type: "string", minLength: 1 },
                  sections: {
                    type: "array",
                    items: { type: "string", minLength: 1 },
                    maxItems: 20,
                  },
                },
                required: ["path"],
                additionalProperties: false,
              },
            ],
          },
          maxItems: 30,
          description:
            "Additional workspace-relative instruction files selected by Codex. Use {path, sections} for large documents; a string means read the whole file. Missing files fail before the agent starts.",
        },
        critical_constraints: {
          type: "array",
          items: { type: "string", minLength: 1 },
          maxItems: 30,
          description:
            "Compact non-secret constraints selected by Codex. Do not paste full instruction files here.",
        },
        task_spec: {
          type: "object",
          description:
            "Optional v1 task design from Codex. Decisions, acceptance criteria, and check commands are rendered into the worker's initial task and the normalized spec is persisted for ds_verify_agent. Omit to keep the legacy prompt-only behavior.",
          properties: {
            version: { type: "integer", const: 1 },
            design_decisions: {
              type: "array",
              items: { type: "string", minLength: 1, maxLength: 2000 },
              maxItems: 50,
            },
            acceptance_criteria: {
              type: "array",
              items: { type: "string", minLength: 1, maxLength: 2000 },
              maxItems: 50,
            },
            checks: {
              type: "array",
              maxItems: 20,
              items: {
                type: "object",
                properties: {
                  id: { type: "string", minLength: 1, maxLength: 100, description: "Unique check ID persisted for verification." },
                  argv: {
                    type: "array",
                    items: { type: "string", minLength: 1 },
                    minItems: 1,
                    description: "Command and arguments. Empty entries and NUL characters are rejected.",
                  },
                  cwd: {
                    type: "string", minLength: 1, maxLength: 500, default: ".",
                    description: "Workspace-relative working directory; absolute paths and traversal are rejected.",
                  },
                  timeout_ms: { type: "integer", minimum: 1, maximum: 300000, default: 60000 },
                },
                required: ["id", "argv"],
                additionalProperties: false,
              },
            },
          },
          required: ["version"],
          additionalProperties: false,
        },
      },
      required: ["task", "workspace"],
      additionalProperties: false,
    },
  },
  {
    name: "ds_model_defaults",
    description:
      "Get, set, or reset the persistent machine-wide model default for newly spawned workers. Existing workers keep their saved selection. set validates against the local OpenCode catalog before saving; get never writes.",
    inputSchema: modelDefaultsSchema,
  },
  {
    name: "ds_verify_agent",
    description:
      "Run the task_spec checks persisted at spawn time for an idle worker. Never accepts commands from this call or from worker reports; starts a controller-local asynchronous job (wait up to 55s), returns job/check IDs, exit codes, timeout or cancellation indicators, and log file paths. Repeated calls return the same running/completed job; rerun=true starts a fresh job only when none is active.",
    inputSchema: {
      type: "object",
      properties: {
        agent_id: agentID,
        wait_ms: { type: "integer", minimum: 0, maximum: 55000, default: 0 },
        rerun: { type: "boolean", default: false },
      },
      required: ["agent_id"],
      additionalProperties: false,
    },
  },
  {
    name: "ds_send_message",
    description:
      "Send a correction or follow-up to an existing coding worker. If it is busy, queue the message for the next idle boundary.",
    inputSchema: {
      type: "object",
      properties: {
        agent_id: agentID,
        message: { type: "string", minLength: 1 },
        queue_if_busy: { type: "boolean", default: true },
      },
      required: ["agent_id", "message"],
      additionalProperties: false,
    },
  },
  {
    name: "ds_wait_agent",
    description:
      "Wait up to 55 seconds for new output, completion, retry, or a permission/question that needs controller attention. Returns compact status by default; pass detail=full for the complete message snapshot. Reuse the returned cursor on the next wait.",
    inputSchema: {
      type: "object",
      properties: {
        agent_id: agentID,
        cursor,
        timeout_ms: { type: "integer", minimum: 0, maximum: 55000, default: 30000 },
        detail,
        return_on: {
          type: "string",
          enum: ["legacy", "actionable"],
          default: "legacy",
          description:
            "legacy (default) keeps the historical payload behavior. actionable consumes transient provider retries internally and escalates to needs_attention with reason provider_retry_budget only after a continuous retry streak reaches 120000ms; an ordinary timeout then returns only agent_id, state=timed_out, and the cursor.",
        },
      },
      required: ["agent_id"],
      additionalProperties: false,
    },
  },
  {
    name: "ds_inspect_agent",
    description:
      "Inspect status, pending requests, cumulative token/cost usage, and the final assistant report for a coding worker. compact (default) hides intermediate turns, prompts, and the instruction manifest; pass detail=full for the legacy message-level snapshot and complete report.",
    inputSchema: {
      type: "object",
      properties: {
        agent_id: agentID,
        cursor,
        message_limit: { type: "integer", minimum: 1, maximum: 100, default: 20 },
        include_diff: { type: "boolean", default: false },
        detail,
      },
      required: ["agent_id"],
      additionalProperties: false,
    },
  },
  {
    name: "ds_fork_agent",
    description:
      "Fork an idle worker conversation from its current state or a message. The fork shares the same filesystem, so parent and child must run sequentially.",
    inputSchema: {
      type: "object",
      properties: { agent_id: agentID, message_id: { type: ["string", "null"] } },
      required: ["agent_id"],
      additionalProperties: false,
    },
  },
  {
    name: "ds_interrupt_agent",
    description: "Abort the current worker turn while preserving its session for a later correction.",
    inputSchema: {
      type: "object",
      properties: { agent_id: agentID },
      required: ["agent_id"],
      additionalProperties: false,
    },
  },
  {
    name: "ds_reply_agent",
    description:
      "Reply to a pending worker permission or question request. Grant permissions only within the user's existing authorization.",
    inputSchema: {
      type: "object",
      properties: {
        agent_id: agentID,
        kind: { type: "string", enum: ["permission", "question"] },
        request_id: { type: "string", pattern: "^(per|que)[A-Za-z0-9_-]+$" },
        reply: { type: "string", enum: ["once", "always", "reject", "answer"] },
        answers: {
          type: "array",
          items: { type: "array", items: { type: "string" } },
        },
        message: { type: "string" },
      },
      required: ["agent_id", "kind", "request_id", "reply"],
      additionalProperties: false,
    },
  },
  {
    name: "ds_list_agents",
    description: "List locally recorded coding workers so a Codex session can resume control after restart.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "ds_close_agent",
    description:
      "Close a coding worker. Isolated worktree removal is optional and must only be requested after useful changes are preserved.",
    inputSchema: {
      type: "object",
      properties: {
        agent_id: agentID,
        remove_worktree: { type: "boolean", default: false },
      },
      required: ["agent_id"],
      additionalProperties: false,
    },
  },
];

export async function callTool(name, args = {}) {
  switch (name) {
    case "ds_check":
      return await controller.check(args);
    case "ds_spawn_agent":
      return await controller.spawnAgent(args);
    case "ds_verify_agent":
      return await controller.verifyAgent(args);
    case "ds_model_defaults":
      return await controller.modelDefaults(args);
    case "ds_send_message":
      return await controller.sendMessage(args);
    case "ds_wait_agent":
      return await controller.waitAgent(args);
    case "ds_inspect_agent":
      return await controller.inspectAgent(args);
    case "ds_fork_agent":
      return await controller.forkAgent(args);
    case "ds_interrupt_agent":
      return await controller.interruptAgent(args);
    case "ds_reply_agent":
      return await controller.replyAgent(args);
    case "ds_list_agents":
      return await controller.listAgents();
    case "ds_close_agent":
      return await controller.closeAgent(args);
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

function write(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function serializeToolResult(result) {
  const text = JSON.stringify(result, null, 2);
  const maximum = 100_000;
  if (text.length <= maximum) return text;
  return JSON.stringify(
    {
      truncated: true,
      preview: text.slice(0, maximum),
      hint: "Inspect the returned worktree directly or request fewer messages without include_diff.",
    },
    null,
    2,
  );
}

function success(id, result) {
  write({ jsonrpc: "2.0", id, result });
}

function failure(id, error) {
  write({
    jsonrpc: "2.0",
    id,
    error: { code: -32000, message: error instanceof Error ? error.message : String(error) },
  });
}

async function handle(message) {
  if (!message || message.jsonrpc !== "2.0") return;
  if (message.method === "notifications/initialized") return;
  if (message.method === "initialize") {
    success(message.id, {
      protocolVersion: message.params?.protocolVersion ?? "2025-06-18",
      capabilities: { tools: { listChanged: false } },
      serverInfo: { name: "deepseek-code-agent", version: "0.1.0" },
    });
    return;
  }
  if (message.method === "ping") {
    success(message.id, {});
    return;
  }
  if (message.method === "tools/list") {
    success(message.id, { tools });
    return;
  }
  if (message.method === "tools/call") {
    try {
      const result = await callTool(message.params?.name, message.params?.arguments ?? {});
      success(message.id, {
        content: [{ type: "text", text: serializeToolResult(result) }],
      });
    } catch (error) {
      success(message.id, {
        content: [
          {
            type: "text",
            text: JSON.stringify({ error: error instanceof Error ? error.message : String(error) }),
          },
        ],
        isError: true,
      });
    }
    return;
  }
  if (message.id !== undefined) {
    failure(message.id, new Error(`Unsupported method: ${message.method}`));
  }
}

function shutdown() {
  controller.shutdown();
}

export function startServer() {
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  input.on("line", (line) => {
    if (!line.trim()) return;
    let message;
    try {
      message = JSON.parse(line);
    } catch (error) {
      failure(null, error);
      return;
    }
    void handle(message).catch((error) => failure(message.id ?? null, error));
  });
  process.on("SIGINT", () => {
    shutdown();
    process.exit(0);
  });
  process.on("SIGTERM", () => {
    shutdown();
    process.exit(0);
  });
  process.on("exit", shutdown);
}

const isDirectExecution =
  process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url;
if (isDirectExecution) startServer();
