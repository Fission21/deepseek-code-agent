"""Stdio MCP adapter for local DSH agents. No HTTP server or extra dependencies."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading

from agent_api import AgentAPI, DEFAULT_ROOT

STRING = {'type': 'string'}
LONG_STRING = {'type': 'string', 'minLength': 1, 'maxLength': 12000}
AGENT_ID = {
    'type': 'string',
    'pattern': '^dsh-[0-9a-f]{32}$',
    'description': 'ID returned by spawn_agent',
}
AGENT = {'agent_id': AGENT_ID}
CURSOR = {'cursor': {'type': 'string', 'description': 'Last returned cursor; excludes messages and results already delivered'}}


def tool(name, description, properties, required=(), readonly=False):
    return {
        'name': name,
        'description': description,
        'inputSchema': {
            'type': 'object',
            'properties': properties,
            'required': list(required),
            'additionalProperties': False,
        },
        'annotations': {'readOnlyHint': readonly, 'openWorldHint': not readonly},
    }


TOOLS = [
    tool('spawn_agent', 'Delegate a development, investigation, review, test, or browser task to a local DSH agent. This is the default external worker lane: DeepSeek Flash with max reasoning owns implementation, tests, fixes and delivery. Returns immediately; use wait_agent for messages and results. Project AGENTS.md rules load natively.', {
        'task': dict(LONG_STRING, description='Goal, context, ownership and expected result, as for a native subagent'),
        'workspace': dict(STRING, description='Absolute existing working directory'),
        'title': {'type': 'string', 'maxLength': 200},
        'scope_paths': {'type': 'array', 'items': STRING, 'maxItems': 100, 'description': 'Writable paths relative to workspace; omitted means read-only. Assignment boundary, not a security sandbox.'},
        'context_files': {'type': 'array', 'items': STRING, 'maxItems': 30, 'description': 'Optional relevant Markdown/text files; never credentials or private Codex session logs'},
        'required_reads': {'type': 'array', 'items': STRING, 'maxItems': 30, 'description': 'Optional applicable skill or rule files beyond native AGENTS.md'},
        'timeout_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 86400, 'default': 600},
        'tab_id': {'type': 'integer', 'minimum': 1, 'description': 'Optional assigned existing Chrome tab; enables huashu MCP and requires url'},
        'url': {'type': 'string', 'pattern': '^https?://', 'description': 'Expected URL for the assigned Chrome tab, without embedded credentials'},
    }, ('task', 'workspace')),
    tool('send_message', 'Send context or a correction to this agent. Running agents receive it at the next native model boundary. Idle or interrupted agents resume the same DSH session. Does not interrupt an in-flight tool.', {
        **AGENT, 'message': LONG_STRING,
    }, ('agent_id', 'message')),
    tool('wait_agent', 'Wait for new progress, a question, completion or failure. Pass the previous cursor to avoid repeated output. At most 55 seconds per call.', {
        **AGENT, **CURSOR,
        'timeout_seconds': {'type': 'number', 'minimum': 0, 'maximum': 55, 'default': 30},
    }, ('agent_id',), True),
    tool('inspect_agent', 'Get compact current status, messages, pending questions and any new final result without waiting.', {
        **AGENT, **CURSOR,
    }, ('agent_id',), True),
    tool('reply_agent', 'Answer a pending question from DSH. It continues the same execution. The supervising agent should resolve routine decisions itself; ask the user only for genuinely missing user decisions.', {
        **AGENT,
        'request_id': {'type': 'string', 'pattern': '^q-[0-9a-f]{32}$'},
        'answer': LONG_STRING,
    }, ('agent_id', 'request_id', 'answer')),
    tool('interrupt_agent', 'Stop the current DSH execution and pause its queue, preserving the session. Waits for owned process cleanup; send_message continues it with a correction.', {
        **AGENT, **CURSOR,
    }, ('agent_id',)),
    tool('list_agents', 'List agents managed by this DSH adapter.', {}, (), True),
    tool('close_agent', 'Close this agent when collaboration is finished. Stops owned execution and keeps its session and result records. Closed agents cannot receive new work.', {
        **AGENT, **CURSOR,
    }, ('agent_id',)),
]
TOOL_NAMES = {item['name'] for item in TOOLS}


def dispatch(api, request):
    method = request.get('method')
    if method == 'initialize':
        version = request.get('params', {}).get('protocolVersion', '2024-11-05')
        if version not in ('2024-11-05', '2025-03-26', '2025-06-18'):
            version = '2025-06-18'
        return {
            'protocolVersion': version,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'dsh-agents', 'version': '0.2.0'},
            'instructions': (
                'Use DSH as the default collaborating worker. Lead, send corrections and answer '
                'questions; DSH owns implementation, testing and repairs. No DSH-specific second '
                'verification workflow is required. Legacy ds_* tools are a separate explicit lane.'
            ),
        }
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': TOOLS}
    if method == 'tools/call':
        params = request.get('params', {})
        if params.get('name') not in TOOL_NAMES:
            raise ValueError('Unknown DSH tool')
        try:
            result = api.call(params['name'], params.get('arguments', {}))
            return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}]}
        except (ValueError, KeyError, OSError, RuntimeError, TypeError) as exc:
            return {
                'isError': True,
                'content': [{'type': 'text', 'text': json.dumps({'error': type(exc).__name__, 'detail': str(exc)}, ensure_ascii=False)}],
            }
    raise ValueError('Unknown MCP method')


def serve(api):
    output_lock = threading.Lock()

    def respond(request):
        try:
            response = {'jsonrpc': '2.0', 'id': request['id'], 'result': dispatch(api, request)}
        except (ValueError, KeyError, TypeError) as exc:
            response = {'jsonrpc': '2.0', 'id': request.get('id'), 'error': {'code': -32602, 'message': str(exc)}}
        with output_lock:
            print(json.dumps(response, ensure_ascii=False), flush=True)

    # A waiting call must not block a concurrent reply or interrupt.
    with ThreadPoolExecutor(max_workers=16) as executor:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if isinstance(request, dict) and 'id' in request:
                    executor.submit(respond, request)
            except ValueError:
                with output_lock:
                    print(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON'}}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    serve(AgentAPI(args.root))
