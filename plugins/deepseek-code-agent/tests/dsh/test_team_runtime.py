import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from support import SKILL as ROOT, POSIX_ONLY, watchdog_stopped
sys.path.insert(0,str(ROOT/'scripts'))
from agent_api import AgentAPI
from scheduler import Scheduler, process_alive

FAKE=r'''
import sys,json,time,pathlib,re,os
p=sys.stdin.read(); a=sys.argv; config=None
for i,x in enumerate(a):
 if x=='--patch':
  try: data=json.loads(pathlib.Path(a[i+1]).read_text())
  except ValueError: continue
  for item in data:
   for plugin in item.get('insert',[]):
    if plugin['id']=='codex-controller-link':config=plugin['config']
assert config
spec=json.loads(re.search(r'FIXTURE (\{[^\n]+\})',p).group(1))
session=a[a.index('--session-id')+1] if '--session-id' in a else 'session-'+config['task_id']
print(json.dumps({'type':'session','sessionId':session}),flush=True)
name=config['task_id']; path=pathlib.Path(name+'.run.json')
old=json.loads(path.read_text()) if path.exists() else {'count':0}
record={'count':old['count']+1,'start':time.time(),'session':session,'pid':os.getpid()}
path.write_text(json.dumps(record))
while spec.get('release_file') and not pathlib.Path(spec['release_file']).exists():time.sleep(.02)
time.sleep(spec.get('delay',.25))
if spec.get('requires'):
 assert spec['requires'] in p, 'Missing versioned dependency context'
for target,value in spec.get('files',{}).items():pathlib.Path(target).write_text(value)
record['end']=time.time();path.write_text(json.dumps(record))
if not spec.get('missing_completion'):
 pathlib.Path(config['mailbox'],'completion.json').write_text(json.dumps({'invocation':config['invocation'],'outcome':spec.get('outcome','done'),'summary':'fixture '+name,'checks':'fixture checked'}))
print(json.dumps({'type':'final','text':'fixture finished'}),flush=True)
'''


@POSIX_ONLY
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='dsh-runtime-test-')
        self.base=Path(self.tmp.name);self.work=self.base/'work';self.work.mkdir()
        self.fake=self.base/'fake-dsh';self.fake.write_text('#!'+sys.executable+'\n'+FAKE);self.fake.chmod(0o700)
        self.api=AgentAPI(self.base/'registry');self.s=Scheduler(self.api.root);self.teams=[]

    def tearDown(self):
        for t in self.teams:self.api.call('close_team',{'team_id':t})
        until=time.monotonic()+8
        while time.monotonic()<until:
            with self.s.db() as db:pid=db.execute("SELECT value FROM settings WHERE key='daemon_pid'").fetchone()[0]
            if not process_alive(pid,self.api.root) and watchdog_stopped(self.api.root):break
            time.sleep(.05)
        self.assertFalse(process_alive(pid,self.api.root),'Test scheduler did not stop')
        self.assertTrue(watchdog_stopped(self.api.root),'Test watchdog did not stop')
        self.tmp.cleanup()

    def spec(self,name,delay=.25,**kw):
        fixture=kw.pop('fixture',{})
        return dict(id=name,workspace=str(self.work),task='FIXTURE '+json.dumps(dict(delay=delay,**fixture)),dsh=str(self.fake),timeout_seconds=15,**kw)

    def create(self,tasks,**options):
        r=self.api.call('create_team',{'tasks':tasks,**options});self.teams.append(r['team_id']);return r['team_id']

    def wait(self,team,statuses=('done','needs_attention'),seconds=10):
        end=time.monotonic()+seconds;r={}
        while time.monotonic()<end:
            r=self.api.call('inspect_team',{'team_id':team})
            if r['state'] in statuses and all(t['status'] not in ('running','cancelling','queued') for t in r['tasks']):return r
            time.sleep(.05)
        self.fail('Team did not settle: '+json.dumps(r))

    def wait_started(self,name):
        end=time.monotonic()+8
        while not (self.work/(name+'.run.json')).exists() and time.monotonic()<end:time.sleep(.03)
        return json.loads((self.work/(name+'.run.json')).read_text())

    def test_real_processes_overlap_and_share_limit_across_teams(self):
        one=self.create([self.spec('a',.7),self.spec('b',.7)])
        two=self.create([self.spec('c',.7)])
        self.assertEqual(self.wait(one)['state'],'done');self.assertEqual(self.wait(two)['state'],'done')
        a,b,c=[json.loads((self.work/(n+'.run.json')).read_text()) for n in 'abc']
        # Fair dispatch may run c before b; assert actual overlap and global cap.
        events=sorted([(r['start'],1) for r in (a,b,c)]+[(r['end'],-1) for r in (a,b,c)])
        live=peak=0
        for at,delta in events:
            live+=delta;peak=max(peak,live)
            self.assertLessEqual(live,2)
        self.assertEqual(peak,2)
        self.assertTrue(all(r['count']==1 for r in (a,b,c)))

    def test_live_team_limit_changes_preserve_jobs_and_other_teams_progress(self):
        one=self.create([self.spec('a',fixture={'release_file':'release-a'}),
                         self.spec('b',fixture={'release_file':'release-b'}),self.spec('d')],max_parallel=1)
        self.wait_started('a')
        two=self.create([self.spec('c')],max_parallel=1)
        self.assertEqual(self.wait(two)['state'],'done')
        self.assertFalse((self.work/'b.run.json').exists())
        view=self.api.call('inspect_team',{'team_id':one})
        self.assertEqual(view['tasks'][1]['waiting_reasons'],[{'code':'team_capacity'}])
        self.api.call('set_team_concurrency',{'team_id':one,'limit':2})
        self.wait_started('b')
        self.api.call('set_team_concurrency',{'team_id':one,'limit':1})
        (self.work/'release-a').touch()
        end=time.monotonic()+5
        while time.monotonic()<end:
            view=self.api.call('inspect_team',{'team_id':one})
            if view['tasks'][0]['status']=='done':break
            time.sleep(.03)
        self.assertEqual(view['tasks'][0]['status'],'done')
        self.assertEqual(view['tasks'][1]['status'],'running')
        self.assertEqual(view['tasks'][2]['status'],'queued')
        self.assertFalse((self.work/'d.run.json').exists())
        (self.work/'release-b').touch()
        self.assertEqual(self.wait(one)['state'],'done')
        a,b,c,d=[json.loads((self.work/(n+'.run.json')).read_text()) for n in 'abcd']
        self.assertLess(c['end'],a['end'])
        self.assertLess(b['start'],a['end'])
        self.assertGreaterEqual(d['start'],b['end'])
        self.assertTrue(all(r['count']==1 for r in (a,b,c,d)))
        self.assertEqual(self.api.call('inspect_team',{'team_id':two})['capacity']['team_limit'],1)
        self.assertEqual(view['concurrency'],2)

    def test_dependencies_receive_versioned_output_and_revision_invalidates_descendants(self):
        team=self.create([self.spec('a',scope_paths=['out.txt'],outputs=['out.txt'],fixture={'files':{'out.txt':'one'}}),
                          self.spec('b',depends_on=['a'],fixture={'requires':'sha256'})])
        r=self.wait(team);self.assertEqual(r['state'],'done',r)
        a=json.loads((self.work/'a.run.json').read_text());b=json.loads((self.work/'b.run.json').read_text())
        self.assertGreaterEqual(b['start'],a['end'])
        self.api.call('resume_task',{'team_id':team,'task_id':'a','message':'FIXTURE '+json.dumps({'files':{'out.txt':'two'}})})
        r=self.wait(team);self.assertEqual(r['tasks'][1]['status'],'stale',r)
        a2=json.loads((self.work/'a.run.json').read_text())
        self.assertEqual(a2['session'],a['session']);self.assertEqual(a2['count'],2)
        self.api.call('resume_task',{'team_id':team,'task_id':'b','message':'FIXTURE '+json.dumps({'requires':'sha256'})})
        self.assertEqual(self.wait(team)['state'],'done')

    def test_failure_blocks_only_dependents_without_replay(self):
        team=self.create([self.spec('a',fixture={'outcome':'failed'}),self.spec('b',depends_on=['a']),self.spec('c')])
        r=self.wait(team);self.assertEqual([t['status'] for t in r['tasks']],['failed','blocked_dependency','done'])
        self.assertEqual(json.loads((self.work/'a.run.json').read_text())['count'],1)
        self.assertFalse((self.work/'b.run.json').exists())

    def test_cancel_releases_slots_and_resume_keeps_session(self):
        team=self.create([self.spec('a',30),self.spec('b',30)])
        a=self.wait_started('a');self.wait_started('b')
        self.api.call('cancel_team',{'team_id':team})
        end=time.monotonic()+5
        while time.monotonic()<end:
            r=self.api.call('inspect_team',{'team_id':team})
            if all(t['status']=='cancelled' for t in r['tasks']):break
            time.sleep(.05)
        self.assertTrue(all(t['status']=='cancelled' for t in r['tasks']),r)
        self.api.call('resume_task',{'team_id':team,'task_id':'a','message':'FIXTURE {}'})
        self.wait(team)
        again=json.loads((self.work/'a.run.json').read_text());self.assertEqual(again['session'],a['session'])

    def test_scheduler_restart_observes_existing_execution_not_replays_it(self):
        team=self.create([self.spec('a',1.2)])
        self.wait_started('a')
        with self.s.db() as db:pid=db.execute("SELECT value FROM settings WHERE key='daemon_pid'").fetchone()[0]
        os.kill(pid,signal.SIGKILL)
        self.assertEqual(self.wait(team)['state'],'done')
        self.assertEqual(json.loads((self.work/'a.run.json').read_text())['count'],1)

    def test_control_request_is_not_blocked_by_saturated_wait_pool(self):
        code="import sys,time;sys.path.insert(0,sys.argv[1]);from mcp_server import serve\nclass API:\n def call(self,m,p):\n  if m=='wait_team':time.sleep(.5)\n  return {}\nserve(API())"
        p=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'scripts')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            for n in range(8):p.stdin.write(json.dumps({'jsonrpc':'2.0','id':n,'method':'tools/call','params':{'name':'wait_team','arguments':{}}})+'\n')
            p.stdin.write(json.dumps({'jsonrpc':'2.0','id':99,'method':'ping'})+'\n');p.stdin.flush()
            self.assertEqual(json.loads(p.stdout.readline())['id'],99)
        finally:p.stdin.close();p.wait(timeout=5);p.stdout.close()


if __name__=='__main__':unittest.main()
