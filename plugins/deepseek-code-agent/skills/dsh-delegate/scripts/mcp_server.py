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
LIMITS = {'type':'object','properties':{name:{'type':kind,'exclusiveMinimum':0} for name,kind in (
    ('max_failures','integer'),('max_elapsed_seconds','number'),('progress_timeout_seconds','number'),
    ('tool_timeout_seconds','number'),('max_peer_messages','integer'),('max_peer_rounds','integer'))},'additionalProperties':False}
ACCEPTANCE = {'type':'object','properties':{
    'criteria':{'type':'array','items':LONG_STRING,'minItems':1},
    'commands':{'type':'array','items':{'type':'array','items':STRING,'minItems':1},'maxItems':20},
    'paths':{'type':'array','items':STRING,'description':'Explicit source files/directories covered by the commands; required with commands'}
},'required':['criteria'],'additionalProperties':False}

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
    tool('spawn_agent', 'Delegate a development, investigation, review, test, or browser task to a local DSH agent. Use only when the user explicitly selects DSH: DeepSeek Flash with max reasoning owns implementation, tests, fixes and delivery. Returns immediately; use wait_agent for messages and results. Project AGENTS.md rules load natively.', {
        'task': dict(LONG_STRING, description='Goal, context, ownership and expected result, as for a native subagent'),
        'workspace': dict(STRING, description='Absolute existing working directory'),
        'title': {'type': 'string', 'maxLength': 200},
        'scope_paths': {'type': 'array', 'items': STRING, 'maxItems': 100, 'description': 'Writable paths relative to workspace; omitted means read-only. Assignment boundary, not a security sandbox.'},
        'context_files': {'type': 'array', 'items': STRING, 'maxItems': 30, 'description': 'Optional relevant Markdown/text files; never credentials or private Codex session logs'},
        'required_reads': {'type': 'array', 'items': STRING, 'maxItems': 30, 'description': 'Optional applicable skill or rule files beyond native AGENTS.md'},
        'acceptance': ACCEPTANCE, 'limits':LIMITS,
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
TEAM = {'team_id': STRING}
TASK_SCHEMA = {'type':'object','properties':{
    **TOOLS[0]['inputSchema']['properties'],
    'id':dict(STRING,description='Unique short task name within this team'),
    'depends_on':{'type':'array','items':STRING,'description':'Task names that must deliver done before this starts'},
    'outputs':{'type':'array','items':STRING,'description':'Expected workspace-relative output files, versioned for dependents'},
    'resources':{'type':'array','items':STRING,'description':'Exclusive shared resource names, for example a test tenant or database'}
},'required':['id','task','workspace'],'additionalProperties':False}
TOOLS += [
    tool('create_team','Start explicitly authorized DSH tasks as a team. Independent tasks run concurrently; dependencies and resource conflicts are scheduled automatically. Default global concurrency is 2. DSH workers own their testing, repairs and complete_task delivery.',
         {'title':STRING,'contract':dict(LONG_STRING,description='Parent-defined immutable shared interface contract; resolve disputes with parent'),'max_parallel':{'type':'integer','minimum':1,'maximum':8,'description':'Optional per-team ceiling within the global limit; omitted means inherit global. Choose based on independent work, not difficulty alone.'},'tasks':{'type':'array','items':TASK_SCHEMA,'minItems':1,'maxItems':32}},('tasks',)),
    tool('inspect_team','Read team tasks, results, capacity, waiting reasons and new events without waiting. eligible_now describes current constraints, not a reservation or an artifact integrity check.',{**TEAM,'cursor':{'type':'integer','minimum':0}},('team_id',),True),
    tool('wait_team','Wait for changes from any task in this team, without one wait call per worker. Returns progress, peer messages, questions and results.',
         {**TEAM,'cursor':{'type':'integer','minimum':0},'timeout_seconds':{'type':'number','minimum':0,'maximum':55}},('team_id',),True),
    tool('resume_task','Continue a stopped or finished team task in the same DSH session. Give the correction to its existing owner. Revising an upstream task invalidates already executed descendants.',
         {**TEAM,'task_id':STRING,'message':STRING,'expected_revision':{'type':'integer','minimum':1},'acknowledge_escalation':{'type':'boolean'},'limits':LIMITS},('team_id','task_id','message','expected_revision')),
    tool('cancel_team','Pause this team and stop its owned executions. Session history remains available for resume_task.',TEAM,('team_id',)),
    tool('close_team','Finish this team, stop owned executions and retain results. It cannot be resumed.',TEAM,('team_id',)),
    tool('set_concurrency','Set the global DSH execution limit for this shared registry across chats. Reducing it lets running jobs finish and delays new starts.',
         {'limit':{'type':'integer','minimum':1,'maximum':8}},('limit',)),
    tool('set_team_concurrency','Adjust only this team ceiling within the global limit. Null restores inheritance. Lowering it does not stop running tasks. Prefer this to changing capacity for other chats.',
         {**TEAM,'limit':{'type':['integer','null'],'minimum':1,'maximum':8}},('team_id','limit'))
]
TOOLS += [
    tool('review_team','Record the parent delivery decision against current task revisions. Use existing acceptance evidence and review important changes; this does not rerun checks or transfer repairs to the parent.',
         {**TEAM,'revisions':{'type':'object','additionalProperties':{'type':'integer','minimum':1}},'summary':LONG_STRING},('team_id','revisions','summary')),
    tool('authorize_browser_action','Grant one exact pending browser action already authorized by the user. Inspect browser_requests first; ordinary replies do not grant actions. Expires in 60 seconds and is consumed before execution.',
         {**AGENT,'request_id':STRING,'expected_revision':{'type':'integer','minimum':1},'reason':LONG_STRING},('agent_id','request_id','expected_revision','reason')),
]
LEGACY_TOOLS = TOOLS
by_name = {item['name']:item for item in LEGACY_TOOLS}
submission = dict(by_name['create_team']['inputSchema']['properties'],
    review_policy={'type':'string','enum':['independent','light'],'default':'independent'},
    review_reason=dict(LONG_STRING,description='Required for low-risk light review; explain why a fresh reviewer is unnecessary'),
    budget_pool=dict(STRING,description='Explicitly configured shared budget pool; otherwise registry default'))
TOOLS = [
    tool('submit_work','Submit explicitly authorized DSH work with requirements and acceptance. Independent review uses a fresh read-only session; light review requires a risk reason. An authorized shared budget must already be configured.',submission,('tasks',)),
    tool('observe_work','List teams or wait for decisions and overall completion. Routine progress remains in the ledger. Pass cursor for deduplication. Long-poll only: this cannot wake an idle host chat; timeout can still cause a model round.',{
        **TEAM,'cursor':{'type':'object','properties':{'seq':{'type':'integer','minimum':0},'terminal':{'type':['string','null']}},'additionalProperties':False},
        'timeout_seconds':{'type':'number','minimum':0,'maximum':55,'default':0}},(),True),
    tool('send_message','Send a correction to the owner. For a stopped task use its expected_revision; acknowledge_escalation explicitly reopens a failed-task circuit. Running tasks receive ordinary messages at model boundaries.',{
        **AGENT,'message':LONG_STRING,'expected_revision':{'type':'integer','minimum':1},
        'acknowledge_escalation':{'type':'boolean'},'limits':LIMITS},('agent_id','message')),
    by_name['reply_agent'],
    tool('stop_work','Pause the team and stop its owned executions; preserve sessions.',TEAM,('team_id',)),
    by_name['authorize_browser_action'],
    tool('deliver_work','Record the parent delivery decision. Requires current revisions, evidence, and the selected independent/light requirement review. Findings go back to their owner.',{
        **by_name['review_team']['inputSchema']['properties'],
        'legacy_review_reason':dict(LONG_STRING,description='Only pre-0.5 tasks: reasoned parent requirement review of existing results, without rerunning implementation')},('team_id','revisions','summary')),
    tool('close_work','Close the team and retain results after collaboration ends.',TEAM,('team_id',)),
]
# Old connected clients can finish their work. Legacy tools are callable but are
# deliberately absent from new discovery; advanced diagnostics stay on the CLI.
TOOL_NAMES = {item['name'] for item in TOOLS+LEGACY_TOOLS}


def dispatch(api, request):
    method = request.get('method')
    if method == 'initialize':
        version = request.get('params', {}).get('protocolVersion', '2024-11-05')
        if version not in ('2024-11-05', '2025-03-26', '2025-06-18'):
            version = '2025-06-18'
        return {
            'protocolVersion': version,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'dsh-agents', 'version': '0.5.0'},
            'instructions': (
                'Use DSH only when the user explicitly requests this collaborating worker. Lead, send corrections and answer '
                'questions; DSH owns implementation, testing and repairs. Use risk-based requirement review without '
                'repeating valid checks. observe_work is long-poll, not host push. Legacy ds_* tools are a separate explicit lane.'
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
    with ThreadPoolExecutor(max_workers=8) as executor, ThreadPoolExecutor(max_workers=4) as waiters:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if isinstance(request, dict) and 'id' in request:
                    name=request.get('params',{}).get('name')
                    pool=waiters if request.get('method')=='tools/call' and name in ('wait_agent','wait_team','observe_work') else executor
                    pool.submit(respond, request)
            except ValueError:
                with output_lock:
                    print(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON'}}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    serve(AgentAPI(args.root))
