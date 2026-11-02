"""Structured DSH subagent operations; also used by the MCP server."""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import urlsplit

from paths import require_supported
from worker import read_state, locked, save

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = Path.home() / '.local/share/dsh-delegate/agents'
AGENT_ID = re.compile(r'dsh-[0-9a-f]{32}')
REQUEST_ID = re.compile(r'q-[0-9a-f]{32}')


def agent_path(root, agent_id):
    if not isinstance(agent_id, str) or not AGENT_ID.fullmatch(agent_id):
        raise ValueError('Invalid DSH agent ID')
    path = root / agent_id
    if path.is_symlink():
        raise ValueError('Agent directory must not be a symlink')
    return path


def init_mailbox(root):
    for name in ('inbox', 'delivered', 'outbox', 'questions', 'replies'):
        (root / 'mailbox' / name).mkdir(parents=True, exist_ok=True, mode=0o700)


def bounded(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise ValueError('Message must contain 1–12000 characters')
    return value


def _string_list(value, field, maximum=100):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('%s must be an array of at most %d strings' % (field, maximum))
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError('%s entries must be non-empty strings' % field)
    return value


def reply(root, request_id, answer):
    if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        raise ValueError('Invalid request ID')
    answer = bounded(answer)
    with locked(root) as state:
        if not state.get('active'):
            raise ValueError('Agent is no longer waiting in an active turn')
        question_path = root / 'mailbox/questions' / (request_id + '.json')
        try:
            question = json.loads(question_path.read_text())
        except FileNotFoundError:
            raise ValueError('Unknown or expired question request') from None
        except ValueError:
            raise ValueError('Question record is malformed') from None
        if question.get('invocation') != state['active'].get('result_file'):
            raise ValueError('Question belongs to a previous execution')
        target = root / 'mailbox/replies' / (request_id + '.json')
        if target.exists():
            if json.loads(target.read_text())['answer'] != answer:
                raise ValueError('This request already has a different reply')
            return {'replied': request_id, 'replayed': True}
        if question['status'] != 'pending':
            raise ValueError('Question is no longer pending')
        save(target, {'request_id': request_id, 'answer': answer})
    return {'replied': request_id}


def _message_seq(message):
    seq = message.get('seq')
    return seq if isinstance(seq, int) and seq >= 0 else None


def _message_order(item):
    path, message_id, message = item
    seq = _message_seq(message)
    if seq is not None:
        return (1, seq, message_id)
    return (0, path.name)


def snapshot(root, cursor=None):
    state = read_state(root)
    previous = {}
    if cursor:
        if not isinstance(cursor, str):
            raise ValueError('Invalid cursor')
        try:
            raw = cursor.encode()
            raw += b'=' * (-len(raw) % 4)
            previous = json.loads(base64.urlsafe_b64decode(raw))
        except (ValueError, TypeError):
            raise ValueError('Invalid cursor') from None
        if not isinstance(previous, dict):
            raise ValueError('Invalid cursor')
    last_id = previous.get('message', '')
    if not isinstance(last_id, str):
        raise ValueError('Invalid cursor')
    delivered_raw = previous.get('delivered')
    if delivered_raw is None:
        delivered = None
    elif isinstance(delivered_raw, list) and all(isinstance(value, str) for value in delivered_raw):
        delivered = set(delivered_raw)
    else:
        raise ValueError('Invalid cursor')

    items = []
    for path in sorted((root / 'mailbox/outbox').glob('*.json')):
        message = json.loads(path.read_text())
        message_id = message.get('id') or path.stem
        if not isinstance(message_id, str):
            raise ValueError('Malformed outbox message')
        items.append((path, message_id, message))
    items.sort(key=_message_order)

    selected = []
    next_delivered = set(delivered or ())
    for path, message_id, message in items:
        if delivered is not None:
            if message_id in delivered:
                continue
            selected.append((path, message_id, message))
        elif _message_seq(message) is not None or path.stem > last_id:
            # A legacy cursor predates sequenced messages. Any sequenced message
            # must be delivered once; unsequenced records keep their old ordering.
            selected.append((path, message_id, message))
        else:
            next_delivered.add(message_id)
    for _, message_id, _ in selected:
        next_delivered.add(message_id)

    messages = [message for _, _, message in selected]
    questions = []
    for path in sorted((root / 'mailbox/questions').glob('*.json')):
        question = json.loads(path.read_text())
        if (state.get('active') and question.get('invocation') == state['active'].get('result_file')
                and question.get('status') == 'pending'
                and not (root / 'mailbox/replies' / path.name).exists()):
            questions.append(question)
    browser_requests=[]
    for file in (root/'mailbox/browser').glob('b-*.json'):
        request=json.loads(file.read_text())
        if (state.get('active') and request.get('invocation')==state['active'].get('result_file')
                and not file.with_suffix('.used').exists() and time.time()-request.get('created',0)<600):
            browser_requests.append(request)
    result = state.get('last_result')
    report = None
    if result and result['turn'] != previous.get('result'):
        path = Path(result['path'])
        if path.parent == root / 'turns' and path.is_file() and path.stat().st_size:
            try:
                report = json.loads(path.read_text())
            except ValueError:
                report = None
    latest_id = selected[-1][1] if selected else last_id
    seq_values = [seq for _, _, message in items for seq in [_message_seq(message)] if seq is not None]
    next_cursor = {
        # `message` stays for legacy readers; `delivered` is the exact dedup set
        # used by this adapter. It never advances past a message that has not
        # actually been returned, even if a lower sequence lands late.
        'message': latest_id,
        'message_seq': max(seq_values) if seq_values else previous.get('message_seq'),
        'delivered': sorted(next_delivered),
        'result': result['turn'] if result else previous.get('result'),
        'state': state['state'],
    }
    output = {
        'agent_id': root.name,
        'state': 'waiting_for_parent' if questions else state['state'],
        'session_id': state.get('session_id'),
        'active': state.get('active'),
        'pending_turns': len(state['queue']),
        'messages': messages,
        'pending_questions': questions,
        'browser_requests':browser_requests,
        'progress': state.get('last_event'),
        'cursor': base64.urlsafe_b64encode(json.dumps(next_cursor).encode()).decode(),
    }
    if report:
        output['result'] = {
            'status': report['status'],
            'text': report.get('final', ''),
            'report_file': str(path),
            'elapsed_seconds': report.get('elapsed_seconds'),
            'acceptance':report.get('acceptance'),
        }
    if state.get('error'):
        output['error'] = state['error']
    return output


class AgentAPI:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root).expanduser().absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def command(self, *args):
        p = subprocess.run(
            [sys.executable, str(HERE / 'worker.py'), *map(str, args)],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        )
        try:
            result = json.loads(p.stdout)
        except ValueError:
            raise RuntimeError('DSH controller failed without a structured response') from None
        if p.returncode:
            raise ValueError(result.get('detail') or result.get('error') or 'Worker command failed')
        return result

    def task_command(self, command, root, task, extra=()):
        fd, name = tempfile.mkstemp(prefix='.dispatch-', suffix='.txt', dir=self.root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(bounded(task))
            return self.command(command, '--state-dir', root, '--task-file', name, *extra)
        finally:
            Path(name).unlink(missing_ok=True)

    def call(self, method, params):
        if os.name != 'posix':
            if method in ('list_agents', 'inspect_agent', 'wait_agent'):
                return self._raw_call(method, params)
            require_supported()
        from scheduler import Scheduler, LIVE, READY
        scheduler = Scheduler(self.root)
        if method=='review_team':
            return scheduler.review_team(params['team_id'],params['revisions'],params['summary'])
        if method=='authorize_browser_action':
            from browser_gate import grant
            root=agent_path(self.root,params['agent_id'])
            with scheduler.db(True) as db:
                task=db.execute('SELECT * FROM tasks WHERE agent_id=?',(params['agent_id'],)).fetchone()
                if not task or task['revision']!=params['expected_revision'] or task['status']!='running':
                    raise ValueError('Stale task revision or inactive browser task')
                with locked(root) as state:
                    if not state.get('active'):
                        raise ValueError('Browser execution is no longer active')
                    result=grant(root/'mailbox',params['request_id'],state['active']['result_file'],params['reason'])
                scheduler.emit(db,task['team_id'],task['name'],'browser_authorized',{'request_id':params['request_id']})
            return result
        if method == 'create_team':
            return scheduler.create_team(params)
        if method == 'set_concurrency':
            return scheduler.set_limit(params['limit'])
        if method == 'set_team_concurrency':
            return scheduler.set_team_limit(params['team_id'],params['limit'])
        if method in ('inspect_team', 'wait_team'):
            scheduler.kick()
            until = time.monotonic() + (min(55, max(0,float(params.get('timeout_seconds',30)))) if method=='wait_team' else 0)
            while True:
                r=scheduler.inspect_team(params['team_id'],params.get('cursor',0))
                if r['events'] or r['state']!='running' or time.monotonic()>=until:return r
                time.sleep(.1)
        if method in ('cancel_team', 'close_team', 'resume_task'):
            team=scheduler.inspect_team(params['team_id'])
            if method=='resume_task':
                tasks=[t for t in team['tasks'] if t['name']==params['task_id']]
                if not tasks:raise ValueError('Unknown task in team')
                return scheduler.resume(tasks[0]['agent_id'],params['message'],expected_revision=params.get('expected_revision'),acknowledge_escalation=params.get('acknowledge_escalation',False),new_limits=params.get('limits'))
            with scheduler.db(True) as db:
                db.execute('UPDATE teams SET state=? WHERE id=?',('closed' if method=='close_team' else 'paused',params['team_id']))
            scheduler.cancel([t['agent_id'] for t in team['tasks']],close=method=='close_team')
            return scheduler.inspect_team(params['team_id'])
        if method=='spawn_agent':
            team=scheduler.create_team({'title':params.get('title',''),'tasks':[dict(params,id='task')]},legacy=True)
            aid=team['tasks'][0]['agent_id']
            return dict(snapshot(self.root/aid),team_id=team['team_id'])
        task=scheduler.task(params.get('agent_id',''))
        if not task and params.get('agent_id'):
            task=scheduler.adopt_agent(params['agent_id'])
        if task and method=='send_message':
            return scheduler.deliver_message(task['agent_id'],params['message'])
        if task and method in ('interrupt_agent','close_agent'):
            scheduler.cancel([task['agent_id']],close=method=='close_agent')
            return dict(snapshot(self.root/task['agent_id'],params.get('cursor')),task_status=scheduler.task(task['agent_id'])['status'])
        if task:scheduler.kick()
        return self._raw_call(method,params)

    def _raw_call(self, method, params):
        if method == 'spawn_agent':
            require_supported()
            workspace = Path(params['workspace']).expanduser()
            if not workspace.is_absolute() or not workspace.is_dir():
                raise ValueError('workspace must be an existing absolute directory')
            task = bounded(params['task'])
            timeout = params.get('timeout_seconds', 600)
            try:
                timeout = float(timeout)
            except (TypeError, ValueError):
                raise ValueError('timeout_seconds must be a positive finite number') from None
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError('timeout_seconds must be a positive finite number')
            tab_id = params.get('tab_id')
            url = params.get('url')
            if (tab_id is None) != (url is None):
                raise ValueError('Browser tasks require both tab_id and url')
            if tab_id is not None:
                if not isinstance(tab_id, int) or tab_id <= 0 or not isinstance(url, str):
                    raise ValueError('tab_id must be a positive integer and url a string')
                parsed = urlsplit(url)
                if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('url must be an http(s) URL without embedded credentials')
            scopes = _string_list(params.get('scope_paths'), 'scope_paths')
            contexts = _string_list(params.get('context_files'), 'context_files', 30)
            reads = _string_list(params.get('required_reads'), 'required_reads', 30)
            agent_id = params.get('_agent_id') or 'dsh-' + uuid.uuid4().hex
            root = agent_path(self.root, agent_id)
            extra = ['--cwd', str(workspace.resolve()), '--controller-link', '--turn-timeout', str(timeout)]
            if params.get('_defer'):
                extra += ['--defer']
            for path in scopes:
                extra += ['--allow-write', path]
            for path in contexts:
                source = Path(path)
                if not source.is_absolute():
                    source = workspace / source
                extra += ['--context-file', str(source)]
            for path in reads:
                source = Path(path)
                if not source.is_absolute():
                    source = workspace / source
                extra += ['--rule-file', str(source)]
            if tab_id is not None:
                extra += ['--tab-id', str(tab_id), '--url', url]
            if params.get('dsh'):
                extra += ['--dsh', str(params['dsh'])]
            self.task_command('start', root, task, extra)
            save(root / 'metadata.json', {'title': params.get('title', ''), 'created_at': time.time()})
            return snapshot(root)
        if method == 'list_agents':
            agents = []
            for path in sorted(self.root.glob('dsh-*')):
                if path.is_symlink() or not path.is_dir():
                    continue
                try:
                    state = read_state(path)
                    metadata = {}
                    try:
                        metadata = json.loads((path / 'metadata.json').read_text())
                    except (OSError, ValueError):
                        pass
                    agents.append({
                        'agent_id': path.name,
                        'state': state['state'],
                        'session_id': state.get('session_id'),
                        'title': metadata.get('title', ''),
                        'pending_turns': len(state['queue']),
                        'completed_turns': state.get('completed_turns', 0),
                    })
                except (OSError, ValueError):
                    continue
            return {'agents': agents}
        root = agent_path(self.root, params.get('agent_id'))
        if method in ('inspect_agent', 'wait_agent'):
            cursor = params.get('cursor')
            if method == 'wait_agent':
                try:
                    timeout = float(params.get('timeout_seconds', 30))
                except (TypeError, ValueError):
                    raise ValueError('timeout_seconds must be a number') from None
                timeout = min(55, max(0, timeout))
            else:
                timeout = 0
            until = time.monotonic() + timeout
            while True:
                result = snapshot(root, cursor)
                if (result['messages'] or result['pending_questions'] or result['browser_requests'] or result.get('result')
                        or result['state'] in ('idle', 'paused', 'closed', 'needs_attention')
                        or time.monotonic() >= until):
                    return result
                time.sleep(.1)
        if method == 'send_message':
            require_supported()
            message = bounded(params['message'])
            with locked(root) as state:
                if state['closed']:
                    raise ValueError('Agent is closed')
                if state['paused'] and state['active']:
                    raise ValueError('Agent is still stopping; wait for the interrupt operation to finish')
                active = bool(state['active'])
                if active:
                    mid = 'm-' + str(int(time.time() * 1000)) + '-' + str(uuid.uuid4())
                    save(root / 'mailbox/inbox' / (mid + '.json'), {'id': mid, 'message': message})
            if not active:
                self.task_command('send', root, message)
            return {'agent_id': root.name, 'delivery': 'next_model_boundary' if active else 'resumed_session'}
        if method == 'reply_agent':
            return reply(root, params['request_id'], params['answer'])
        if method in ('interrupt_agent', 'close_agent'):
            require_supported()
            self.command('stop' if method == 'interrupt_agent' else 'close', '--state-dir', root)
            self.command('wait', '--state-dir', root, '--timeout', '10')
            return snapshot(root, params.get('cursor'))
        raise ValueError('Unknown agent operation')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    try:
        request = json.load(sys.stdin)
        print(json.dumps(AgentAPI(args.root).call(request['method'], request.get('params', {})), ensure_ascii=False))
    except (ValueError, KeyError, OSError, RuntimeError, TypeError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'detail': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
