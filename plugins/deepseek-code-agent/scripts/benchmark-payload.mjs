// No model calls. Compare serialized queen-facing payloads for an identical transcript.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { DeepSeekController, compactMessages } from '../src/controller.mjs';

const transcriptPath = process.argv[2];
const usage = { total: 1120, input: 100, output: 20, reasoning: 0, cache: { read: 1000, write: 0 } };
const synthetic = [
  { info: { id: 'msg_user', role: 'user' }, parts: [{ type: 'text', text: 'Implement scoped behavior and validate it. '.repeat(30) }] },
  ...Array.from({ length: 12 }, (_, i) => ({
    info: { id: `msg_step${i}`, role: 'assistant', time: { completed: i + 1 }, finish: 'tool-calls', tokens: usage, cost: 0.001 },
    parts: [
      { type: 'text', text: `Investigating module ${i}. `.repeat(20) },
      { type: 'tool', tool: 'read', state: { status: 'completed', title: `src/module${i}.mjs` } },
      { type: 'step-finish', reason: 'tool-calls', tokens: usage, cost: 0.001 },
    ],
  })),
  { info: { id: 'msg_final', role: 'assistant', time: { completed: 20 }, finish: 'stop', tokens: usage, cost: 0.001 }, parts: [{ type: 'text', text: 'Implemented scoped behavior in src/example.mjs. Regression checks passed. No remaining risks. No instruction manifest files.' }] },
];
const messages = transcriptPath ? JSON.parse(await readFile(transcriptPath, 'utf8')) : synthetic;
assert.ok(Array.isArray(messages) && messages.length, 'transcript must be a nonempty OpenCode message array');
assert.equal(messages.at(-1)?.info?.role, 'assistant', 'transcript must end with an assistant result');
const controller = new DeepSeekController();
const state = { directory: '/workspace/example', workspace_mode: 'worktree', scope_paths: ['src'], instruction_manifest: { files: [], total_bytes: 0 } };
controller.readState = async () => state;
controller.rawStatus = async () => ({ type: 'idle' });
controller.pending = async () => ({ permissions: [], questions: [] });
controller.messages = async () => messages;
controller.queueCount = async () => 0;

// Exact legacy snapshot envelope, using the retained legacy message projector.
function legacy(cursor) {
  const projected = compactMessages(messages, cursor, 20);
  return {
    agent_id: 'ses_fixture', state: projected.messages.length ? 'completed' : 'idle',
    opencode_status: { type: 'idle' }, cursor: projected.cursor,
    messages: projected.messages, queued_messages: 0,
    pending: { permissions: [], questions: [] }, worktree: state.directory,
    workspace_mode: state.workspace_mode, scope_paths: state.scope_paths,
    instruction_manifest: state.instruction_manifest,
  };
}
function size(value) {
  const serialized = JSON.stringify(value, null, 2);
  return { characters: serialized.length, utf8_bytes: Buffer.byteLength(serialized) };
}
const original = legacy(null);
const compact = await controller.inspectAgent({ agent_id: 'ses_fixture' });
assert.equal(compact.state, 'completed');
assert.ok(compact.final_report, 'compact view must preserve a final handoff');
assert.deepEqual(compact.pending, original.pending);
const replay = await controller.inspectAgent({ agent_id: 'ses_fixture', cursor: compact.cursor });
assert.ok(!replay.final_report, 'unchanged cursor must not replay the final handoff');
const full = await controller.inspectAgent({ agent_id: 'ses_fixture', detail: 'full' });
assert.ok(full.messages.length, 'explicit full diagnostics must remain available');
const before = size(original);
const after = size(compact);
console.log(JSON.stringify({
  measurement: 'serialized_payload_size_not_model_tokens',
  source: transcriptPath ? 'supplied_transcript' : 'synthetic_12_step_fixture',
  assistant_messages: messages.filter(m => m.info?.role === 'assistant').length,
  legacy: before, compact: after,
  character_reduction_percent: Number((100 * (1 - after.characters / before.characters)).toFixed(2)),
  unchanged_compact: size(replay),
  full_diagnostics: size(full),
  report_truncated: compact.report_truncated,
  note: 'Does not measure queen tokens, reasoning, tool-call overhead, review effort, or end-to-end quality.',
}, null, 2));
