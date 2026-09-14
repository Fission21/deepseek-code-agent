#!/usr/bin/env node
import readline from "node:readline";
import { pathToFileURL } from "node:url";

import { DeepSeekController } from "./src/controller.mjs";

const controller = new DeepSeekController();

const agentID = { type: "string", pattern: "^ses[A-Za-z0-9_-]+$" };
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
      "Check whether OpenCode, the OpenCode Go provider, and DeepSeek V4.1 Flash Max are available on this computer. Does not call the model.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "ds_spawn_agent",
    description:
      "Create a persistent DeepSeek coding agent and submit its initial task asynchronously. Codex remains responsible for review and verification.",
    inputSchema: {
      type: "object",
      properties: {
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
      },
      required: ["task", "workspace"],
      additionalProperties: false,
    },
  },
  {
    name: "ds_send_message",
    description:
      "Send a correction or follow-up to an existing DeepSeek agent. If it is busy, queue the message for the next idle boundary.",
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
      },
      required: ["agent_id"],
      additionalProperties: false,
    },
  },
  {
    name: "ds_inspect_agent",
    description:
      "Inspect status, pending requests, cumulative token/cost usage, and the final assistant report for a DeepSeek agent. compact (default) hides intermediate turns, prompts, and the instruction manifest; pass detail=full for the legacy message-level snapshot and complete report.",
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
      "Fork an idle DeepSeek conversation from its current state or a message. The fork shares the same filesystem, so parent and child must run sequentially.",
    inputSchema: {
      type: "object",
      properties: { agent_id: agentID, message_id: { type: ["string", "null"] } },
      required: ["agent_id"],
      additionalProperties: false,
    },
  },
  {
    name: "ds_interrupt_agent",
    description: "Abort the current DeepSeek turn while preserving its session for a later correction.",
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
      "Reply to a pending DeepSeek permission or question request. Grant permissions only within the user's existing authorization.",
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
    description: "List locally recorded DeepSeek agents so a Codex session can resume control after restart.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "ds_close_agent",
    description:
      "Close a DeepSeek agent. Isolated worktree removal is optional and must only be requested after useful changes are preserved.",
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
      return await controller.check();
    case "ds_spawn_agent":
      return await controller.spawnAgent(args);
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
