"""Local DSH team scheduler: one shared ledger, bounded execution, no model router."""
from contextlib import contextmanager
try:
    import fcntl
except ImportError:
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from context_packet import write_scope
from paths import require_supported
from reliability import acceptance, limits, fingerprint, stalled
from budget import BudgetLedger
from review_policy import expand_review_tasks, validate_review, validate_delivery
from worker import save, read_state, locked, launch, recover_orphan

HERE = Path(__file__).resolve().parent
LIVE = ('running', 'cancelling')
READY = ('queued', 'blocked_dependency')


def overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def process_alive(pid, root):
    if not pid:
        return False
    p = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
    return str(HERE / 'scheduler.py') in p.stdout and str(root) in p.stdout


class Scheduler:
    def __init__(self, root):
        require_supported()
        self.root = Path(root).expanduser().absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.root / 'scheduler.sqlite3'
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER);
                INSERT OR IGNORE INTO settings VALUES ('concurrency',2),('daemon_pid',0);
                CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY,title TEXT,state TEXT,created REAL);
                CREATE TABLE IF NOT EXISTS team_limits (team_id TEXT PRIMARY KEY,max_parallel INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    agent_id TEXT PRIMARY KEY,team_id TEXT,name TEXT,spec TEXT,status TEXT,
                    revision INTEGER DEFAULT 1,attempt INTEGER DEFAULT 0,started REAL,
                    result TEXT,error TEXT,cancel_to TEXT,inputs TEXT,
                    UNIQUE(team_id,name));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,team_id TEXT,task_id TEXT,
                    kind TEXT,data TEXT,created REAL);
                CREATE TABLE IF NOT EXISTS task_health (agent_id TEXT PRIMARY KEY, failures INTEGER DEFAULT 0, elapsed REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS dispatch_order (team_id TEXT PRIMARY KEY, served REAL);
                CREATE TABLE IF NOT EXISTS reviews (team_id TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS receipts (agent_id TEXT,message_id TEXT,
                    PRIMARY KEY(agent_id,message_id));
            ''')
        os.chmod(self.db_path, 0o600)

    @contextmanager
    def db(self, transaction=False):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if transaction:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if transaction:
                db.commit()
        except BaseException:
            if transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def emit(self, db, team, task, kind, data):
        db.execute('INSERT INTO events(team_id,task_id,kind,data,created) VALUES (?,?,?,?,?)',
                   (team, task, kind, json.dumps(data, ensure_ascii=False), time.time()))

    def task(self, agent_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM tasks WHERE agent_id=?', (agent_id,)).fetchone()
        return dict(row) if row else None

    def adopt_agent(self, agent_id):
        """Import a pre-scheduler session without restarting or changing its worker."""
        from agent_api import agent_path
        root=agent_path(self.root,agent_id)
        if (root/'team.json').exists() or not (root/'state.json').exists():return self.task(agent_id)
        state=read_state(root);config=json.loads((root/'config.json').read_text())
        spec={'id':'task','workspace':config['cwd'],'scope_paths':config['allow_write'],
              'tab_id':config.get('tab_id'),'legacy':True,'_agent_id':agent_id}
        status='running' if state['active'] else ('queued' if state['queue'] and not state['paused'] else ('done' if state['state']=='idle' else 'blocked'))
        team='team-'+uuid.uuid4().hex
        with self.db(True) as db:
            if db.execute('SELECT 1 FROM tasks WHERE agent_id=?',(agent_id,)).fetchone():return self.task(agent_id)
            db.execute('INSERT INTO teams VALUES (?,?,?,?)',(team,'Adopted existing agent','closed' if state['closed'] else 'active',time.time()))
            db.execute('INSERT INTO tasks(agent_id,team_id,name,spec,status,attempt) VALUES (?,?,?,?,?,?)',
                       (agent_id,team,'task',json.dumps(spec),status,state.get('completed_turns',0)))
            self.emit(db,team,'task','adopted',{'agent_id':agent_id,'status':status})
        return self.task(agent_id)

    def create_team(self, params, start=True, legacy=False):
        from agent_api import AgentAPI, bounded
        team_limit = params.get('max_parallel')
        if team_limit is not None:
            self.validate_limit(team_limit)
        for path in self.root.glob('dsh-*/state.json'):
            if not (path.parent/'team.json').exists() and read_state(path.parent).get('active'):
                self.adopt_agent(path.parent.name)
        specs = expand_review_tasks(params)
        if not isinstance(params.get('contract', ''), str) or len(params.get('contract', '')) > 12000:
            raise ValueError('Contract must contain at most 12000 characters')
        if not isinstance(specs, list) or not 1 <= len(specs) <= 32:
            raise ValueError('A team requires 1–32 tasks')
        names = [s['id'] for s in specs]
        if len(set(names)) != len(names) or any(not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', n) for n in names):
            raise ValueError('Task IDs must be unique short names')
        graph = {}
        validated=[]
        for s in specs:
            bounded(s['task'])
            workspace = Path(s['workspace']).expanduser()
            if not workspace.is_absolute() or not workspace.is_dir():
                raise ValueError('Workspace must be an existing absolute directory')
            workspace = workspace.resolve()
            s = dict(s, workspace=str(workspace))
            s['budget']=BudgetLedger(self.root).binding(s.get('budget_pool',params.get('budget_pool')))
            s['limits'] = limits(s.get('limits'))
            s['acceptance'] = acceptance(s.get('acceptance'), s['task'], workspace)
            graph[s['id']] = s.get('depends_on', [])
            if not isinstance(graph[s['id']], list) or any(n not in names for n in graph[s['id']]):
                raise ValueError('Dependency must name a task in this team')
            s['scope_paths'] = write_scope(workspace, s.get('scope_paths', []))
            outputs = write_scope(workspace, s.get('outputs', []))
            s['outputs']=outputs
            for out in outputs:
                if not any(overlap(workspace / out, workspace / w) and
                           (workspace / w == workspace / out or workspace / w in (workspace / out).parents)
                           for w in s['scope_paths']):
                    raise ValueError('Each output must be inside the task write scope')
            if not isinstance(s.get('resources', []), list) or any(not isinstance(r, str) or not r for r in s.get('resources', [])):
                raise ValueError('Resources must be named strings')
            validated.append(s)
        specs=validated
        visited, active = set(), set()
        def visit(n):
            if n in active:
                raise ValueError('Dependency cycle')
            if n in visited:
                return
            active.add(n)
            for parent in graph[n]:
                visit(parent)
            active.remove(n); visited.add(n)
        for n in names:
            visit(n)
        team = 'team-' + uuid.uuid4().hex
        prepared = []
        api = AgentAPI(self.root)
        try:
            for source in specs:
                s = dict(source, legacy=legacy)
                s['workspace'] = str(Path(s['workspace']).expanduser().resolve())
                aid = 'dsh-' + uuid.uuid4().hex
                task_root = self.root / aid
                prompt = s['task']
                if not legacy:
                    prompt += ('\n\nTeam task: ' + s['id'] + '. Peers: ' + ', '.join(names) +
                               '. Own implementation, appropriate tests, repairs and delivery. '
                               'Send useful findings directly with send_peer; routine choices are yours. '
                               'Finish with complete_task(outcome=done|blocked|failed, summary, checks). '
                               'Do not report done while work or required checks remain. '
                               'The result releases dependents after the declared acceptance commands pass. '
                               'You own repairs; the parent decides delivery without repeating valid checks.')
                api._raw_call('spawn_agent', dict(s, task=prompt, _agent_id=aid, _defer=True))
                prepared.append(task_root)
                save(task_root / 'team.json', {'team_id':team,'task_id':s['id'],'peers':names,
                                             'budget':s['budget'],
                                             'review_criteria':s.get('review_criteria',[]),
                                             'require_completion':not legacy, 'acceptance':s['acceptance'],
                                             'limits':s['limits'], 'contract':params.get('contract',''), 'contract_revision':1})
                dependency_file = task_root / 'dependencies.txt'
                dependency_file.write_text('No completed dependency inputs yet.\n')
                os.chmod(dependency_file, 0o600)
                config = json.loads((task_root / 'config.json').read_text())
                config['context_files'].append(str(dependency_file))
                save(task_root / 'config.json', config)
                s['_agent_id'] = aid
                source['_prepared_spec'] = s
            with self.db(True) as db:
                db.execute('INSERT INTO teams VALUES (?,?,?,?)', (team, params.get('title',''), 'active', time.time()))
                if team_limit is not None:
                    db.execute('INSERT INTO team_limits VALUES (?,?)', (team, team_limit))
                for source in specs:
                    s = source.pop('_prepared_spec')
                    db.execute('INSERT INTO tasks(agent_id,team_id,name,spec,status) VALUES (?,?,?,?,?)',
                               (s['_agent_id'],team,s['id'],json.dumps(s),'queued'))
                    self.emit(db,team,s['id'],'queued',{'agent_id':s['_agent_id']})
        except BaseException:
            # Only newly prepared, deferred workers belong here; none was launched.
            import shutil
            for path in prepared:
                shutil.rmtree(path)
            for source in specs:
                source.pop('_prepared_spec',None)
            raise
        if start:
            self.kick()
        return self.inspect_team(team)

    def resources(self, task):
        spec = json.loads(task['spec'])
        writes = [Path(spec['workspace']) / x for x in spec.get('scope_paths', [])]
        reads = [Path(a['path']) for item in json.loads(task.get('inputs') or '[]') for a in item.get('artifacts',[])]
        reads += [Path(spec['workspace'])/p for p in spec.get('review_paths',[])]
        keys = set(spec.get('resources', []))
        if spec.get('tab_id') is not None:
            keys.add('browser:huashu:shared-chrome')
        return writes, reads, keys

    def conflict(self, a, b):
        aw, ar, ak = self.resources(a); bw, br, bk = self.resources(b)
        return bool(ak & bk) or any(overlap(x,y) for x in aw for y in bw+br) or any(overlap(x,y) for x in ar for y in bw)

    def dependency_inputs(self, db, task):
        inputs, missing = [], []
        for name in json.loads(task['spec']).get('depends_on', []):
            dep = db.execute('SELECT * FROM tasks WHERE team_id=? AND name=?', (task['team_id'], name)).fetchone()
            if dep['status'] != 'done':
                missing.append({'task_id':name, 'status':dep['status']})
            else:
                result = json.loads(dep['result'] or '{}')
                if json.loads(task['spec']).get('review_targets'):
                    inputs.append(dict(task_id=name,revision=dep['revision'],acceptance=result.get('acceptance'),artifacts=result.get('artifacts',[])))
                else:
                    inputs.append(dict(task_id=name, revision=dep['revision'], summary=result.get('summary',''), rationale=result.get('rationale',''), checks=result.get('checks',''), open_issues=result.get('open_issues',''), acceptance=result.get('acceptance'), artifacts=result.get('artifacts',[])))
        return inputs, missing

    def claim_next(self):
        with self.db(True) as db:
            running = [dict(r) for r in db.execute("SELECT * FROM tasks WHERE status IN ('running','cancelling')")]
            limit = db.execute("SELECT value FROM settings WHERE key='concurrency'").fetchone()[0]
            if len(running) >= limit:
                return None
            team_limits = dict(db.execute('SELECT team_id,max_parallel FROM team_limits'))
            rows = db.execute("SELECT t.* FROM tasks t JOIN teams g ON g.id=t.team_id LEFT JOIN dispatch_order d ON d.team_id=g.id WHERE g.state='active' AND t.status IN ('queued','blocked_dependency') ORDER BY COALESCE(d.served,0),g.created,t.rowid").fetchall()
            for raw in rows:
                t = dict(raw)
                if sum(r['team_id']==t['team_id'] for r in running) >= team_limits.get(t['team_id'], limit):
                    continue
                try:
                    binding=json.loads(t['spec']).get('budget') or BudgetLedger(self.root).binding()
                    BudgetLedger(self.root).admit(binding['pool_id'])
                except ValueError as exc:
                    db.execute("UPDATE tasks SET status='blocked_budget',error=? WHERE agent_id=?",(str(exc),t['agent_id']))
                    self.emit(db,t['team_id'],t['name'],'blocked_budget',{'summary':str(exc)})
                    continue
                inputs, missing = self.dependency_inputs(db, t)
                if missing:
                    if t['status'] != 'blocked_dependency':
                        db.execute("UPDATE tasks SET status='blocked_dependency' WHERE agent_id=?",(t['agent_id'],))
                    continue
                stale = any(not Path(a['path']).is_file() or hashlib.sha256(Path(a['path']).read_bytes()).hexdigest()!=a['sha256']
                            for item in inputs for a in item['artifacts'])
                for item in inputs:
                    producer=db.execute('SELECT * FROM tasks WHERE team_id=? AND name=?',(t['team_id'],item['task_id'])).fetchone()
                    if not self.delivery_current(producer,json.loads(producer['result'] or '{}')):
                        stale=True
                if stale:
                    db.execute("UPDATE tasks SET status='blocked_version',error=? WHERE agent_id=?",('Dependency artifact changed after delivery',t['agent_id']))
                    self.emit(db,t['team_id'],t['name'],'blocked_version',{})
                    continue
                t['inputs']=json.dumps(inputs)
                if any(self.conflict(t,r) for r in running):
                    continue
                spec=json.loads(t['spec'])
                if spec.get('review_targets'):
                    binding=fingerprint(spec['workspace'],spec['review_paths'])
                    for item in inputs:item['review_fingerprint']=binding
                    t['inputs']=json.dumps(inputs)
                db.execute("UPDATE tasks SET status='running',attempt=attempt+1,started=?,inputs=?,error=NULL WHERE agent_id=?",(time.time(),t['inputs'],t['agent_id']))
                t.update(status='running',attempt=t['attempt']+1,started=time.time())
                db.execute('INSERT OR REPLACE INTO dispatch_order VALUES (?,?)',(t['team_id'],time.time()))
                self.emit(db,t['team_id'],t['name'],'running',{'attempt':t['attempt'],'inputs':inputs})
                return t
        return None

    def artifacts(self, task):
        spec=json.loads(task['spec']); workspace=Path(spec['workspace']).resolve(); result=[]
        for name in spec.get('outputs', []):
            p=(workspace/name).resolve()
            if workspace not in p.parents or not p.is_file():
                raise ValueError('Declared output is missing or escapes workspace: '+name)
            result.append({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        return result

    def completion(self, task, last):
        spec=json.loads(task['spec'])
        if last.get('status')!='completed':
            return 'failed',{'summary':'Execution did not complete: '+str(last.get('status'))}
        try:
            report=json.loads(Path(last['path']).read_text())
            if not isinstance(report,dict):
                raise ValueError('Invalid report')
        except (OSError,ValueError,KeyError):
            return 'blocked',{'summary':'Execution report unavailable; inspect before resuming'}
        if spec.get('legacy'):
            result={'summary':report.get('final',''),'artifacts':[]}
            outcome='done'
        else:
            p=self.root/task['agent_id']/'mailbox/completion.json'
            try:
                result=json.loads(p.read_text())
                if not isinstance(result,dict):
                    raise ValueError('Invalid outcome')
            except (OSError,ValueError):
                return 'blocked',{'summary':'Missing or invalid complete_task outcome; resume the owner to finish delivery'}
            if result.get('invocation')!=last['path']:
                return 'blocked',{'summary':'Completion belongs to an earlier execution'}
            outcome=result.get('outcome')
            if outcome not in ('done','blocked','failed'):
                return 'blocked',{'summary':'Invalid completion outcome'}
        # Honest blockers/failures retain their explanation and do not require
        # checks the runner deliberately skipped for incomplete work.
        if outcome!='done':
            return outcome,result
        required=spec.get('acceptance',{}).get('commands')
        if required:
            evidence=report.get('acceptance') or {}
            expected=spec['acceptance']
            try:
                valid=(isinstance(evidence,dict) and evidence.get('passed')
                       and evidence.get('commands')==expected['commands']
                       and evidence.get('fingerprint')==fingerprint(spec['workspace'],expected['paths']))
            except (OSError,ValueError):
                valid=False
            if not valid:
                return 'failed',{'summary':'Declared acceptance failed or evidence is stale','acceptance':evidence}
        if not spec.get('legacy'):
            try:
                result['artifacts']=self.artifacts(task)
            except (OSError,ValueError) as exc:
                return 'blocked',{'summary':str(exc)}
        result['acceptance']=report.get('acceptance')
        try: validate_review(self,task,result)
        except (OSError,ValueError) as exc:return 'blocked',dict(result,summary=str(exc))
        return outcome,result

    def finish(self, task, status, result):
        with self.db(True) as db:
            current=db.execute('SELECT * FROM tasks WHERE agent_id=?',(task['agent_id'],)).fetchone()
            if current['status'] not in LIVE or current['attempt'] != task['attempt']:
                return False
            if current['status']=='running':
                root=self.root/task['agent_id']
                with locked(root) as state:
                    pending=list((root/'mailbox/inbox').glob('*.json')) or (state['queue'] and state.get('last_result'))
                    if state['active'] or (pending and not state['paused'] and not state['closed']):
                        return False
            if current['status']=='cancelling':
                status=current['cancel_to'] or 'cancelled'
            health=db.execute('SELECT failures,elapsed FROM task_health WHERE agent_id=?',(task['agent_id'],)).fetchone()
            failures=(health[0] if health else 0)+(1 if status=='failed' else 0)
            elapsed=(health[1] if health else 0)+max(0,time.time()-(current['started'] or time.time()))
            policy=limits(json.loads(current['spec']).get('limits'))
            db.execute('INSERT OR REPLACE INTO task_health VALUES (?,?,?)',(task['agent_id'],failures,elapsed))
            if status=='failed' and (failures>=policy['max_failures'] or elapsed>=policy['max_elapsed_seconds']):
                status='escalated'
                result=dict(result,escalation='Execution budget exhausted; parent must acknowledge before resuming')
            db.execute('UPDATE tasks SET status=?,result=?,error=?,cancel_to=NULL WHERE agent_id=?',
                       (status,json.dumps(result,ensure_ascii=False),None if status=='done' else result.get('summary'),task['agent_id']))
            self.emit(db,task['team_id'],task['name'],status,result)
        return True

    def settle_inactive(self, agent_id):
        # The worker may publish its result before the scheduler's next tick.
        # Reconcile only this finished execution before deciding how to resume.
        task=self.task(agent_id)
        if task and task['status'] in LIVE:
            state=read_state(self.root/agent_id)
            if not state['active'] and state['state'] in ('idle','paused','closed'):
                status,result=self.completion(task,state.get('last_result') or {})
                self.finish(task,status,result)

    def deliver_message(self, agent_id, message):
        from agent_api import bounded
        bounded(message)
        self.settle_inactive(agent_id)
        with self.db(True) as db:
            row=db.execute('SELECT * FROM tasks WHERE agent_id=?',(agent_id,)).fetchone()
            if not row:raise ValueError('Unknown managed agent')
            if row['status']=='cancelling':raise ValueError('Agent is still stopping')
            legacy=json.loads(row['spec']).get('legacy',False)
            active=row['status'] in LIVE+READY
            if active:
                root=self.root/agent_id
                with locked(root) as state:
                    if state['closed']:raise ValueError('Agent is closed')
                    mid='m-'+str(int(time.time()*1000))+'-'+uuid.uuid4().hex
                    save(root/'mailbox/inbox'/(mid+'.json'),{'id':mid,'message':message})
        if active:
            self.kick()
            return {'agent_id':agent_id,'delivery':'next_model_boundary' if legacy else 'queued_for_model_boundary'}
        result=self.resume(agent_id,message)
        if legacy:
            result.update(delivery='resumed_session',scheduling='queued')
        return result

    def recover_quiescing_worker(self, root):
        with open(root/'worker.lock','a') as lease:
            try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            with locked(root) as state:
                if not state['active'] and state['state']=='running':
                    state['state']='closed' if state['closed'] else ('paused' if state['paused'] else ('queued' if state['queue'] else 'idle'))
                    state['controller_pid']=None

    def ingest(self, task, message):
        # The DB transaction and stable destination filename make retries idempotent.
        with self.db(True) as db:
            if db.execute('SELECT 1 FROM receipts WHERE agent_id=? AND message_id=?',(task['agent_id'],message['id'])).fetchone():
                return
            kind=message.get('kind','progress')
            if kind=='peer':
                policy=limits(json.loads(task['spec']).get('limits'))
                prior=[json.loads(r[0]) for r in db.execute("SELECT data FROM events WHERE team_id=? AND task_id=? AND kind='peer'",(task['team_id'],task['name']))]
                event_type=message.get('event_type','artifact_ready')
                correlation=message.get('correlation_id')
                rounds=sum(m.get('correlation_id')==correlation and m.get('event_type')=='question' for m in prior) if correlation else 0
                rejected=(len(prior)>=policy['max_peer_messages'] or event_type not in ('question','answer','blocked','artifact_ready')
                          or (event_type in ('question','answer') and not correlation)
                          or (event_type=='question' and rounds>=policy['max_peer_rounds'])
                          or message.get('contract_revision',1)!=1)
                target=db.execute('SELECT * FROM tasks WHERE team_id=? AND name=?',(task['team_id'],message.get('target'))).fetchone()
                waiting_peer=target and event_type=='question' and target['status'] not in LIVE
                acceptable=target and (target['status'] in LIVE+READY or (target['status']=='done' and event_type in ('blocked','artifact_ready')))
                if rejected or not acceptable or waiting_peer:
                    kind='peer_rejected'
                    message=dict(message,reason='Peer budget/contract rejected or recipient is not running. Never occupy a slot waiting for a queued peer; declare dependencies or ask parent to arbitrate.')
                else:
                    mid='peer-'+hashlib.sha256((task['agent_id']+message['id']).encode()).hexdigest()
                    mailbox=self.root/target['agent_id']/'mailbox'
                    if not (mailbox/'delivered'/(mid+'.json')).exists() and not (mailbox/'inbox'/(mid+'.json')).exists():
                        save(mailbox/'inbox'/(mid+'.json'),{'id':mid,'source':'peer','sender':task['name'],'event_type':event_type,'correlation_id':correlation,'contract_revision':1,'message':message['message']})
                    message=dict(message,delivery='queued_for_model_boundary')
            self.emit(db,task['team_id'],task['name'],kind,message)
            db.execute('INSERT INTO receipts VALUES (?,?)',(task['agent_id'],message['id']))

    def tick(self):
        with self.db() as db:
            tasks=[dict(r) for r in db.execute('SELECT * FROM tasks')]
        for t in tasks:
            root=self.root/t['agent_id']
            for p in sorted((root/'mailbox/outbox').glob('*.json')):
                self.ingest(t,json.loads(p.read_text()))
            for request in sorted((root/'mailbox/browser').glob('b-*.json')):
                data=json.loads(request.read_text())
                self.ingest(t,dict(data,kind='browser_request',message='Browser action awaits parent authorization'))
            if t['status'] not in LIVE:
                continue
            state=read_state(root)
            if state['active']:
                # This is a no-op while the worker owns its lease, and reconciles a crashed worker.
                recover_orphan(root)
                state=read_state(root)
                if state['active']:
                    policy=limits(json.loads(t['spec']).get('limits'))
                    reason=stalled(root,state,policy)
                    with self.db() as db:
                        health=db.execute('SELECT elapsed FROM task_health WHERE agent_id=?',(t['agent_id'],)).fetchone()
                    if (health[0] if health else 0)+time.time()-(t['started'] or time.time())>=policy['max_elapsed_seconds']:
                        reason='Total execution time budget exhausted'
                    if reason:
                        self.escalate(t,reason)
                    continue
            if state['state']=='running':
                self.recover_quiescing_worker(root)
                state=read_state(root)
            if t['status']=='running' and not state['paused'] and not state['closed'] and (
                    state['queue'] or list((root/'mailbox/inbox').glob('*.json'))):
                self.start_worker(t)
                continue
            if state['state']=='queued' and t['status']=='running':
                self.start_worker(t)
                continue
            if state['state'] in ('idle','paused','closed'):
                status,result=self.completion(t,state.get('last_result') or {})
                self.finish(t,status,result)
        while True:
            t=self.claim_next()
            if not t:
                break
            self.start_worker(t)
        with self.db() as db:
            return bool(db.execute("SELECT 1 FROM tasks WHERE status IN ('running','cancelling','queued') LIMIT 1").fetchone())

    def escalate(self, task, reason):
        from agent_api import AgentAPI
        with self.db(True) as db:
            current=db.execute('SELECT status,attempt FROM tasks WHERE agent_id=?',(task['agent_id'],)).fetchone()
            if not current or current['status']!='running' or current['attempt']!=task['attempt']:
                return
            db.execute("UPDATE tasks SET status='cancelling',cancel_to='escalated' WHERE agent_id=?",(task['agent_id'],))
            self.emit(db,task['team_id'],task['name'],'watchdog',{'reason':reason})
        AgentAPI(self.root)._raw_call('interrupt_agent',{'agent_id':task['agent_id']})
        self.finish(task,'escalated',{'summary':reason,'requires_parent':True})

    def start_worker(self, task):
        root=self.root/task['agent_id']
        (root/'dependencies.txt').write_text('Versioned dependency deliveries:\n'+(task['inputs'] or '[]'),encoding='utf-8')
        launch(root)

    def inspect_team(self, team, cursor=0):
        cursor=int(cursor)
        with self.db() as db:
            # One read snapshot: counts and waiting reasons describe the same moment.
            db.execute('BEGIN')
            g=db.execute('SELECT * FROM teams WHERE id=?',(team,)).fetchone()
            if not g:
                raise ValueError('Unknown team')
            limit=db.execute("SELECT value FROM settings WHERE key='concurrency'").fetchone()[0]
            configured=db.execute('SELECT max_parallel FROM team_limits WHERE team_id=?',(team,)).fetchone()
            team_limit=configured[0] if configured else None
            effective=min(limit,team_limit) if team_limit is not None else limit
            running=[dict(r) for r in db.execute("SELECT * FROM tasks WHERE status IN ('running','cancelling')")]
            team_running=sum(r['team_id']==team for r in running)
            available=max(0,min(limit-len(running),effective-team_running)) if g['state']=='active' else 0
            tasks=[]
            dependency_ready=resource_ready=0
            active=False
            rows=[dict(r) for r in db.execute('SELECT * FROM tasks WHERE team_id=? ORDER BY rowid',(team,))]
            for r in rows:
                item={k:r[k] for k in ('name','agent_id','status','revision','attempt','error')}
                item['result']=json.loads(r['result']) if r['result'] else None
                health=db.execute('SELECT failures,elapsed FROM task_health WHERE agent_id=?',(r['agent_id'],)).fetchone()
                item['budget']={'failures':health[0] if health else 0,'elapsed_seconds':health[1] if health else 0,'limits':limits(json.loads(r['spec']).get('limits'))}
                reasons=[]
                if r['status'] in LIVE+('queued',):
                    active=True
                if r['status'] in READY:
                    if g['state']!='active':
                        reasons.append({'code':'team_'+g['state']})
                    inputs, missing=self.dependency_inputs(db,r)
                    if missing:
                        reasons.append({'code':'dependencies','tasks':missing})
                    else:
                        active=True
                        dependency_ready+=1
                        r['inputs']=json.dumps(inputs)
                        conflicts=sum(self.conflict(r,other) for other in running)
                        if conflicts:
                            reasons.append({'code':'resources','conflicting_running_tasks':conflicts})
                        else:
                            resource_ready+=1
                    if team_limit is not None and team_running>=team_limit:
                        reasons.append({'code':'team_capacity'})
                    if len(running)>=limit:
                        reasons.append({'code':'global_capacity'})
                elif r['status'] not in ('running','done'):
                    reasons.append({'code':r['status']})
                item['waiting_reasons']=reasons
                item['eligible_now']=r['status'] in READY and not reasons
                tasks.append(item)
            events=[dict(r) for r in db.execute('SELECT seq,task_id,kind,data,created FROM events WHERE team_id=? AND seq>? ORDER BY seq LIMIT 200',(team,cursor))]
            for e in events:e['data']=json.loads(e['data'])
            review=db.execute('SELECT data FROM reviews WHERE team_id=?',(team,)).fetchone()
            review=json.loads(review[0]) if review else {'status':'pending'}
        state=g['state'] if g['state']!='active' else ('running' if active else ('done' if all(t['status']=='done' for t in tasks) else 'needs_attention'))
        capacity={'global_limit':limit,'team_limit':team_limit,'effective_limit':effective,
                  'global_running':len(running),'team_running':team_running,'available_slots':available,
                  'dependency_ready':dependency_ready,'resource_ready':resource_ready}
        return {'team_id':team,'state':state,'concurrency':limit,'capacity':capacity,
                'review':review,'tasks':tasks,'events':events,'cursor':events[-1]['seq'] if events else cursor}

    def resume(self, agent_id, message, expected_revision=None, acknowledge_escalation=False, new_limits=None):
        from agent_api import AgentAPI, bounded
        bounded(message); stop=[]
        with self.db(True) as db:
            row=db.execute('SELECT * FROM tasks WHERE agent_id=?',(agent_id,)).fetchone()
            if not row:
                raise ValueError('Unknown managed agent')
            t=dict(row)
            if expected_revision is not None and t['revision'] != expected_revision:
                raise ValueError('Stale task revision; read current state before writing')
            spec=json.loads(t['spec']); policy=limits(spec.get('limits'))
            spec['budget']=BudgetLedger(self.root).binding(spec.get('budget',{}).get('pool_id'))
            BudgetLedger(self.root).admit(spec['budget']['pool_id'])
            db.execute('UPDATE tasks SET spec=? WHERE agent_id=?',(json.dumps(spec),agent_id))
            health=db.execute('SELECT failures,elapsed FROM task_health WHERE agent_id=?',(agent_id,)).fetchone()
            exhausted=health and (health[0]>=policy['max_failures'] or health[1]>=policy['max_elapsed_seconds'])
            if (t['status']=='escalated' or exhausted) and not acknowledge_escalation:
                raise ValueError('Task circuit is open; parent acknowledgement and revised allocation required')
            if acknowledge_escalation:
                db.execute('DELETE FROM task_health WHERE agent_id=?',(agent_id,))
            if new_limits is not None:
                spec['limits']=limits(dict(policy,**new_limits))
                db.execute('UPDATE tasks SET spec=? WHERE agent_id=?',(json.dumps(spec),agent_id))
            db.execute('DELETE FROM reviews WHERE team_id=?',(t['team_id'],))
            if db.execute('SELECT state FROM teams WHERE id=?',(t['team_id'],)).fetchone()[0]=='closed':
                raise ValueError('Team is closed')
            if t['status'] in LIVE:
                raise ValueError('Interrupt and wait before resuming a running task')
            # A revised producer invalidates already started descendants. Unstarted tasks simply wait.
            all_tasks=[dict(r) for r in db.execute('SELECT * FROM tasks WHERE team_id=?',(t['team_id'],))]
            changed={t['name']}
            while True:
                more={r['name'] for r in all_tasks if set(json.loads(r['spec']).get('depends_on',[])) & changed}
                if more<=changed:break
                changed|=more
            for r in all_tasks:
                if r['name'] not in changed or r['agent_id']==agent_id:continue
                if r['status'] in LIVE:
                    db.execute("UPDATE tasks SET status='cancelling',cancel_to='stale' WHERE agent_id=?",(r['agent_id'],));stop.append(r['agent_id'])
                elif r['attempt']:
                    db.execute("UPDATE tasks SET status='stale',error='Upstream revised; explicitly resume with current inputs' WHERE agent_id=?",(r['agent_id'],))
            root=self.root/agent_id
            with locked(root) as state:
                if state['active'] or state['closed']:
                    raise ValueError('Agent still stopping or closed')
                state['queue']=[{'id':state['next_id'],'text':message,'context_files':[]}]
                state['next_id']+=1
                state.update(paused=False,error=None,state='queued')
            meta=json.loads((root/'team.json').read_text()) if (root/'team.json').exists() else {}
            meta['limits']=spec.get('limits',policy)
            meta['budget']=spec['budget']
            save(root/'team.json',meta)
            db.execute("UPDATE tasks SET status='queued',revision=revision+1,result=NULL,error=NULL WHERE agent_id=?",(agent_id,))
            db.execute("UPDATE teams SET state='active' WHERE id=?",(t['team_id'],))
            self.emit(db,t['team_id'],t['name'],'revised',{})
        for aid in stop:AgentAPI(self.root)._raw_call('interrupt_agent',{'agent_id':aid})
        self.kick()
        return {'agent_id':agent_id,'delivery':'queued_same_session'}

    def delivery_current(self, task, result):
        spec=json.loads(task['spec'])
        required=spec.get('acceptance',{})
        if required.get('commands'):
            evidence=result.get('acceptance') or {}
            try:
                return bool(evidence.get('passed') and evidence.get('fingerprint')==fingerprint(spec['workspace'],required['paths']))
            except (OSError,ValueError):
                return False
        return True

    def review_team(self, team, revisions, summary, legacy_review_reason=None):
        from agent_api import bounded
        bounded(summary)
        if legacy_review_reason is not None:bounded(legacy_review_reason)
        with self.db(True) as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM tasks WHERE team_id=?',(team,))]
            if not rows or any(t['status']!='done' for t in rows):
                raise ValueError('Only a completed team can be delivered')
            if revisions!={t['name']:t['revision'] for t in rows}:
                raise ValueError('Stale task revisions')
            validate_delivery(self,rows,legacy_review_reason)
            for t in rows:
                result=json.loads(t['result'] or '{}')
                if not self.delivery_current(t,result):
                    raise ValueError('Tested source has changed; return the task to its owner')
                for artifact in result.get('artifacts',[]):
                    p=Path(artifact['path'])
                    if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=artifact['sha256']:
                        raise ValueError('Delivered artifact has changed')
            review={'status':'accepted','revisions':revisions,'summary':summary}
            if legacy_review_reason:review['legacy_review_reason']=legacy_review_reason
            db.execute('INSERT OR REPLACE INTO reviews VALUES (?,?)',(team,json.dumps(review)))
            self.emit(db,team,None,'parent_delivery',review)
        return review

    def cancel(self, agent_ids, close=False):
        from agent_api import AgentAPI
        stopping=[]
        with self.db(True) as db:
            for aid in agent_ids:
                t=db.execute('SELECT * FROM tasks WHERE agent_id=?',(aid,)).fetchone()
                if not t:continue
                if t['status']=='done' and not close:
                    continue
                status='cancelling'
                db.execute("UPDATE tasks SET status=?,cancel_to=? WHERE agent_id=?",(status,'done' if t['status']=='done' else 'cancelled',aid))
                self.emit(db,t['team_id'],t['name'],status,{})
                stopping.append(aid)
        api=AgentAPI(self.root)
        from concurrent.futures import ThreadPoolExecutor
        def stop(aid):
            return api._raw_call('close_agent' if close else 'interrupt_agent',{'agent_id':aid})
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(stop,stopping))
        for aid in stopping:
            self.settle_inactive(aid)
        self.kick()

    @staticmethod
    def validate_limit(limit):
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=8:
            raise ValueError('Concurrency must be an integer from 1 to 8')

    def set_team_limit(self, team, limit):
        if limit is not None:
            self.validate_limit(limit)
        with self.db(True) as db:
            group=db.execute('SELECT state FROM teams WHERE id=?',(team,)).fetchone()
            if not group:
                raise ValueError('Unknown team')
            if group['state']=='closed':
                raise ValueError('Team is closed')
            if limit is None:
                db.execute('DELETE FROM team_limits WHERE team_id=?',(team,))
            else:
                db.execute('INSERT OR REPLACE INTO team_limits VALUES (?,?)',(team,limit))
            self.emit(db,team,None,'concurrency_changed',{'max_parallel':limit})
        self.kick()
        return self.inspect_team(team)

    def set_limit(self, limit):
        self.validate_limit(limit)
        with self.db(True) as db:
            db.execute("UPDATE settings SET value=? WHERE key='concurrency'",(limit,))
        self.kick()
        return {'concurrency':limit,'scope':'shared registry across all teams and chats'}

    def kick(self):
        from watchdog import ensure_watchdog
        with self.db(True) as db:
            if not db.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running','cancelling') LIMIT 1").fetchone():return
            pid=db.execute("SELECT value FROM settings WHERE key='daemon_pid'").fetchone()[0]
            ensure_watchdog(self.root)
            if process_alive(pid,self.root):return
            p=subprocess.Popen([sys.executable,str(HERE/'scheduler.py'),'--root',str(self.root)],stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,
                               env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
            db.execute("UPDATE settings SET value=? WHERE key='daemon_pid'",(p.pid,))
            threading.Thread(target=p.wait,daemon=True).start()

    def run(self):
        with open(self.root/'scheduler.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:
                while True:
                    save(self.root/'scheduler-heartbeat.json',{'pid':os.getpid(),'at':time.time()})
                    busy=self.tick()
                    if not busy:
                        with self.db(True) as db:
                            if db.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running','cancelling') LIMIT 1").fetchone():continue
                            db.execute("UPDATE settings SET value=0 WHERE key='daemon_pid' AND value=?",(os.getpid(),))
                        return
                    time.sleep(.2)
            finally:
                with self.db(True) as db:
                    db.execute("UPDATE settings SET value=0 WHERE key='daemon_pid' AND value=?",(os.getpid(),))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    Scheduler(parser.parse_args().root).run()
