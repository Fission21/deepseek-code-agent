import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from support import SKILL as ROOT, POSIX_ONLY, install_fake
sys.path.insert(0, str(ROOT / 'scripts'))


@POSIX_ONLY
class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / 'scripts/scheduler.py').exists(), 'Concurrent scheduler is missing')
        from scheduler import Scheduler
        self.tmp = tempfile.TemporaryDirectory(prefix='dsh-team-test-')
        self.base = Path(self.tmp.name)
        self.workspace = self.base / 'work'; self.workspace.mkdir()
        self.fake = install_fake(self.base / 'fake-dsh')
        self.scheduler = Scheduler(self.base / 'registry')
        from support import fund_fixture
        fund_fixture(self.scheduler.root)

    def tearDown(self):
        if hasattr(self, 'tmp'): self.tmp.cleanup()

    def spec(self, name, **kwargs):
        return dict(id=name, task='Complete fixture task', workspace=str(self.workspace), dsh=str(self.fake), **kwargs)

    def test_reject_cycle_before_creating_workers(self):
        with self.assertRaises(ValueError):
            self.scheduler.create_team({'tasks':[self.spec('a', depends_on=['b']), self.spec('b', depends_on=['a'])]}, start=False)
        self.assertEqual(list((self.base / 'registry').glob('dsh-*')), [])

    def test_atomic_global_limit_applies_across_teams(self):
        a=self.scheduler.create_team({'tasks':[self.spec('a'), self.spec('b')]}, start=False)
        b=self.scheduler.create_team({'tasks':[self.spec('c')]}, start=False)
        claims=[self.scheduler.claim_next(), self.scheduler.claim_next(), self.scheduler.claim_next()]
        self.assertEqual(sum(x is not None for x in claims), 2)
        self.assertEqual(len({x['agent_id'] for x in claims if x}), 2)

    def test_team_limit_leaves_spare_global_slot_for_another_team(self):
        one=self.scheduler.create_team({'max_parallel':1,'tasks':[self.spec('a'),self.spec('b')]},start=False)
        two=self.scheduler.create_team({'tasks':[self.spec('c')]},start=False)
        a,c=self.scheduler.claim_next(),self.scheduler.claim_next()
        self.assertEqual((a['team_id'],c['team_id']),(one['team_id'],two['team_id']))
        self.assertIsNone(self.scheduler.claim_next())
        view=self.scheduler.inspect_team(one['team_id'])
        self.assertEqual(view['capacity']['team_limit'],1)
        self.assertEqual(view['capacity']['global_running'],2)
        self.assertEqual(view['capacity']['team_running'],1)
        self.assertEqual({r['code'] for r in view['tasks'][1]['waiting_reasons']},{'team_capacity','global_capacity'})

    def test_team_limit_changes_do_not_interrupt_running_tasks_or_change_global_limit(self):
        from agent_api import AgentAPI
        from unittest.mock import patch
        team=self.scheduler.create_team({'max_parallel':1,'tasks':[self.spec(n) for n in 'abc']},start=False)['team_id']
        api=AgentAPI(self.base/'registry')
        a=self.scheduler.claim_next()
        self.assertIsNone(self.scheduler.claim_next())
        with patch('scheduler.Scheduler.kick'):
            api.call('set_team_concurrency',{'team_id':team,'limit':2})
        b=self.scheduler.claim_next();self.assertEqual(b['name'],'b')
        with patch('scheduler.Scheduler.kick'):
            api.call('set_team_concurrency',{'team_id':team,'limit':1})
        self.assertEqual(self.scheduler.task(a['agent_id'])['status'],'running')
        self.assertEqual(self.scheduler.task(b['agent_id'])['status'],'running')
        self.scheduler.finish(a,'done',{})
        self.assertIsNone(self.scheduler.claim_next())
        self.scheduler.finish(b,'done',{})
        self.assertEqual(self.scheduler.claim_next()['name'],'c')
        self.assertEqual(self.scheduler.inspect_team(team)['concurrency'],2)
        with patch('scheduler.Scheduler.kick'):
            result=api.call('set_team_concurrency',{'team_id':team,'limit':None})
        self.assertIsNone(result['capacity']['team_limit'])
        self.assertEqual(result['capacity']['effective_limit'],2)

    def test_team_limit_cannot_exceed_global_capacity(self):
        team=self.scheduler.create_team({'max_parallel':8,'tasks':[self.spec(n) for n in 'abc']},start=False)
        self.scheduler.claim_next();self.scheduler.claim_next()
        self.assertIsNone(self.scheduler.claim_next())
        self.assertEqual(self.scheduler.inspect_team(team['team_id'])['capacity']['effective_limit'],2)

    def test_snapshot_explains_dependencies_and_resources_without_exposing_other_team(self):
        one=self.scheduler.create_team({'tasks':[self.spec('private',scope_paths=['shared.txt'])]},start=False)
        self.scheduler.claim_next()
        two=self.scheduler.create_team({'tasks':[self.spec('dep',depends_on=['writer']),self.spec('writer',scope_paths=['shared.txt']),self.spec('free')]},start=False)
        view=self.scheduler.inspect_team(two['team_id'])
        dep,writer,free=view['tasks']
        self.assertEqual(dep['waiting_reasons'],[{'code':'dependencies','tasks':[{'task_id':'writer','status':'queued'}]}])
        self.assertEqual(writer['waiting_reasons'],[{'code':'resources','conflicting_running_tasks':1}])
        self.assertTrue(free['eligible_now'])
        self.assertFalse(writer['eligible_now'])
        self.assertEqual(view['capacity']['dependency_ready'],2)
        self.assertEqual(view['capacity']['resource_ready'],1)
        self.assertEqual(view['capacity']['available_slots'],1)
        self.assertNotIn(one['team_id'],json.dumps(view))
        self.assertNotIn('private',json.dumps(view))

    def test_snapshot_respects_dependency_artifact_read_locks(self):
        team=self.scheduler.create_team({'tasks':[self.spec('producer',scope_paths=['p.txt'],outputs=['p.txt']),self.spec('consumer',depends_on=['producer'])]},start=False)
        producer=self.scheduler.claim_next();(self.workspace/'p.txt').write_text('v1')
        self.scheduler.finish(producer,'done',{'artifacts':self.scheduler.artifacts(producer)})
        # Another running writer owns the delivered path before the consumer is claimed.
        other=self.scheduler.create_team({'tasks':[self.spec('writer',scope_paths=['p.txt'])]},start=False)
        with self.scheduler.db(True) as db:
            db.execute("UPDATE tasks SET status='running' WHERE team_id=?",(other['team_id'],))
        view=self.scheduler.inspect_team(team['team_id'])
        self.assertEqual(view['tasks'][1]['waiting_reasons'],[{'code':'resources','conflicting_running_tasks':1}])
        self.assertIsNone(self.scheduler.claim_next())

    def test_team_limit_validates_before_creating_workers_and_closed_group_stays_closed(self):
        from unittest.mock import patch
        for limit in (0,9,True,1.5,'2'):
            with self.assertRaises(ValueError):
                self.scheduler.create_team({'max_parallel':limit,'tasks':[self.spec('a')]},start=False)
        self.assertEqual(list((self.base/'registry').glob('dsh-*')),[])
        team=self.scheduler.create_team({'tasks':[self.spec('a')]},start=False)['team_id']
        with self.scheduler.db(True) as db:db.execute("UPDATE teams SET state='closed' WHERE id=?",(team,))
        with patch.object(self.scheduler,'kick'),self.assertRaises(ValueError):self.scheduler.set_team_limit(team,2)
        view=self.scheduler.inspect_team(team)
        self.assertFalse(view['tasks'][0]['eligible_now'])
        self.assertEqual(view['tasks'][0]['waiting_reasons'][0]['code'],'team_closed')

    def test_existing_database_gains_limits_without_rewriting_teams(self):
        import sqlite3
        from scheduler import Scheduler
        root=self.base/'old-registry';root.mkdir()
        with sqlite3.connect(root/'scheduler.sqlite3') as db:
            db.execute('CREATE TABLE teams (id TEXT PRIMARY KEY,title TEXT,state TEXT,created REAL)')
            db.execute("INSERT INTO teams VALUES ('old','old title','active',1)")
        s=Scheduler(root)
        with s.db() as db:
            self.assertEqual(len(db.execute('PRAGMA table_info(teams)').fetchall()),4)
            self.assertEqual(db.execute("SELECT title FROM teams WHERE id='old'").fetchone()[0],'old title')
            self.assertEqual(db.execute('SELECT * FROM team_limits').fetchall(),[])

    def test_concurrent_claimers_cannot_overrun_team_limits(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from scheduler import Scheduler
        self.scheduler.create_team({'max_parallel':1,'tasks':[self.spec(n) for n in 'abc']},start=False)
        self.scheduler.create_team({'max_parallel':1,'tasks':[self.spec(n) for n in 'def']},start=False)
        with self.scheduler.db(True) as db:db.execute("UPDATE settings SET value=4 WHERE key='concurrency'")
        schedulers=[Scheduler(self.base/'registry') for _ in range(6)]
        barrier=Barrier(6)
        def claim(s):
            barrier.wait(timeout=5)
            return s.claim_next()
        with ThreadPoolExecutor(max_workers=6) as pool:claims=[c for c in pool.map(claim,schedulers) if c]
        self.assertEqual(len(claims),2)
        self.assertEqual(len({c['team_id'] for c in claims}),2)

    def test_paused_and_stale_tasks_are_not_reported_as_eligible(self):
        from unittest.mock import patch
        team=self.scheduler.create_team({'tasks':[self.spec('a'),self.spec('b')]},start=False)['team_id']
        with self.scheduler.db(True) as db:
            db.execute("UPDATE teams SET state='paused' WHERE id=?",(team,))
            db.execute("UPDATE tasks SET status='stale' WHERE team_id=? AND name='b'",(team,))
        with patch.object(self.scheduler,'kick'):
            view=self.scheduler.set_team_limit(team,1)
        self.assertEqual(view['state'],'paused')
        self.assertEqual(view['capacity']['available_slots'],0)
        self.assertTrue(all(not t['eligible_now'] for t in view['tasks']))
        self.assertEqual(view['tasks'][0]['waiting_reasons'],[{'code':'team_paused'}])
        self.assertEqual(view['tasks'][1]['waiting_reasons'],[{'code':'stale'}])

    def test_existing_active_agent_is_counted_after_adoption(self):
        from agent_api import AgentAPI
        from worker import locked
        api=AgentAPI(self.base/'registry')
        old=api._raw_call('spawn_agent',dict(self.spec('old'),_defer=True))
        with locked(self.base/'registry'/old['agent_id']) as state:
            state.update(state='running',active={'turn':1,'runner_pid':123,'result_file':'test'})
        self.scheduler.adopt_agent(old['agent_id'])
        self.scheduler.create_team({'tasks':[self.spec('a'),self.spec('b')]},start=False)
        self.assertIsNotNone(self.scheduler.claim_next())
        self.assertIsNone(self.scheduler.claim_next())

    def test_parent_child_write_paths_are_exclusive(self):
        self.scheduler.create_team({'tasks':[self.spec('a',scope_paths=['src']),self.spec('b',scope_paths=['src/a.py'])]},start=False)
        self.assertIsNotNone(self.scheduler.claim_next())
        self.assertIsNone(self.scheduler.claim_next())

    def test_symlink_aliases_cannot_bypass_write_exclusion(self):
        (self.workspace/'real').mkdir();(self.workspace/'alias').symlink_to(self.workspace/'real',target_is_directory=True)
        self.scheduler.create_team({'tasks':[self.spec('a',scope_paths=['alias']),self.spec('b',scope_paths=['real'])]},start=False)
        self.assertIsNotNone(self.scheduler.claim_next())
        self.assertIsNone(self.scheduler.claim_next())

    def complete_worker_state(self, task, state_name='idle'):
        from worker import locked,save
        root=self.base/'registry'/task['agent_id']; result=root/'turns/00001.result.json'
        save(result,{'status':'completed','final':'done'})
        save(root/'mailbox/completion.json',{'invocation':str(result),'outcome':'done','summary':'done','checks':'done'})
        with locked(root) as state:
            state.update(state=state_name,queue=[],active=None,last_result={'path':str(result),'status':'completed','turn':1})

    def test_late_message_is_not_lost_at_idle_boundary(self):
        from agent_api import AgentAPI
        self.scheduler.create_team({'tasks':[self.spec('a')]},start=False)
        a=self.scheduler.claim_next();self.complete_worker_state(a)
        from unittest.mock import patch
        with patch('scheduler.Scheduler.kick'):
            AgentAPI(self.base/'registry').call('send_message',{'agent_id':a['agent_id'],'message':'late correction'})
        # Prevent actual execution while observing whether it is rescheduled.
        started=[];self.scheduler.start_worker=lambda t:started.append(t['agent_id'])
        self.scheduler.tick()
        self.assertEqual(self.scheduler.task(a['agent_id'])['status'],'running')
        self.assertEqual(started,[a['agent_id']])

    def test_crash_after_result_commit_releases_capacity(self):
        self.scheduler.create_team({'tasks':[self.spec('a'),self.spec('b'),self.spec('c')]},start=False)
        a,b=self.scheduler.claim_next(),self.scheduler.claim_next()
        self.complete_worker_state(a,'running');self.complete_worker_state(b,'running')
        started=[];self.scheduler.start_worker=lambda t:started.append(t['name'])
        self.scheduler.tick()
        self.assertEqual(self.scheduler.task(a['agent_id'])['status'],'done')
        self.assertEqual(started,['c'])

    def test_queued_cancel_is_not_resumable_until_stop_finishes(self):
        from agent_api import AgentAPI
        from unittest.mock import patch
        team=self.scheduler.create_team({'tasks':[self.spec('a')]},start=False)
        aid=team['tasks'][0]['agent_id'];refused=[]
        original=AgentAPI._raw_call
        def stop(api,method,params):
            if method=='interrupt_agent':
                try:self.scheduler.resume(aid,'new revision')
                except ValueError:refused.append(True)
                return {}
            return original(api,method,params)
        with patch.object(AgentAPI,'_raw_call',stop),patch.object(self.scheduler,'kick'):
            self.scheduler.cancel([aid])
        self.assertEqual(refused,[True])

    def test_shared_chrome_tasks_are_serial_even_with_different_tabs(self):
        import os
        from unittest.mock import patch
        package=self.base/'huashu-chrome';(package/'src').mkdir(parents=True)
        (package/'package.json').write_text(json.dumps({'name':'huashu-chrome','version':'1.3.0'}))
        (package/'src/cli.js').write_text('// offline scheduling fixture; never launched')
        with patch.dict(os.environ,{'HUASHU_CHROME_ENTRY':str(package/'src/cli.js'),'HUASHU_CHROME_VERSION':'1.3.0'}):
            self.scheduler.create_team({'tasks':[self.spec('a',tab_id=1,url='https://example.com'), self.spec('b',tab_id=2,url='https://example.com')]},start=False)
        self.assertIsNotNone(self.scheduler.claim_next())
        self.assertIsNone(self.scheduler.claim_next())

    def test_dependency_waits_and_changed_artifact_blocks_consumer(self):
        team=self.scheduler.create_team({'tasks':[self.spec('a',scope_paths=['a.txt'],outputs=['a.txt']),self.spec('b',depends_on=['a'])]},start=False)
        a=self.scheduler.claim_next();self.assertEqual(a['name'],'a')
        self.assertIsNone(self.scheduler.claim_next())
        (self.workspace/'a.txt').write_text('v1')
        self.scheduler.finish(a,'done',{'summary':'first','artifacts':self.scheduler.artifacts(a)})
        (self.workspace/'a.txt').write_text('v2')
        self.assertIsNone(self.scheduler.claim_next())
        b=self.scheduler.inspect_team(team['team_id'])['tasks'][1]
        self.assertEqual(b['status'],'blocked_version')

    def test_plain_process_completion_is_not_team_task_completion(self):
        team=self.scheduler.create_team({'tasks':[self.spec('a')]},start=False)
        a=self.scheduler.claim_next()
        result=self.scheduler.completion(a, {'path':'unused','status':'completed'})
        self.assertEqual(result[0],'blocked')

    def test_peer_delivery_is_deduplicated_and_cannot_leave_team(self):
        team=self.scheduler.create_team({'tasks':[self.spec('a'),self.spec('b')]},start=False)
        tasks=self.scheduler.inspect_team(team['team_id'])['tasks']; a,b=tasks
        a=self.scheduler.task(a['agent_id'])
        message={'id':'m-fixture','kind':'peer','target':'b','message':'new finding'}
        self.scheduler.ingest(a,message);self.scheduler.ingest(a,message)
        inbox=self.base/'registry'/b['agent_id']/'mailbox/inbox'
        self.assertEqual(len(list(inbox.glob('*.json'))),1)
        self.scheduler.ingest(a,dict(message,id='m-invalid',target='outside'))
        events=self.scheduler.inspect_team(team['team_id'])['events']
        self.assertTrue(any(e['kind']=='peer_rejected' for e in events))


if __name__=='__main__':unittest.main()
