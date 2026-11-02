import json
import os
import signal
import sys
import time
import test_team_runtime as fixtures


class ReliabilityRuntimeTests(fixtures.RuntimeTests):
    def test_failed_acceptance_blocks_dependent(self):
        (self.work/'source.txt').write_text('source')
        team=self.create([self.spec('a',acceptance={'criteria':['passes'], 'paths':['source.txt'],
            'commands':[[sys.executable,'-c','raise SystemExit(2)']]}),self.spec('b',depends_on=['a'])])
        r=self.wait(team)
        self.assertEqual(r['tasks'][0]['status'],'failed')
        self.assertFalse((self.work/'b.run.json').exists())
        self.assertEqual(r['tasks'][0]['result']['acceptance']['results'][0]['exit_code'],2)

    def test_acceptance_records_one_run_and_rejects_source_change(self):
        (self.work/'source.txt').write_text('before')
        team=self.create([self.spec('a',acceptance={'criteria':['source stable'], 'paths':['source.txt'],
            'commands':[[sys.executable,'-c',"from pathlib import Path; Path('source.txt').write_text('after')"]]})])
        r=self.wait(team)
        self.assertEqual(r['tasks'][0]['status'],'failed')
        self.assertFalse(r['tasks'][0]['result']['acceptance']['passed'])

    def test_progress_deadline_stops_without_replay(self):
        team=self.create([self.spec('a',fixture={'release_file':'never'},limits={'progress_timeout_seconds':2})])
        self.wait_started('a')
        r=self.wait(team)
        self.assertEqual(r['tasks'][0]['status'],'escalated')
        self.assertEqual(json.loads((self.work/'a.run.json').read_text())['count'],1)
        self.assertIn('progress',r['tasks'][0]['error'])

    def test_watchdog_recovers_without_parent_poll(self):
        team=self.create([self.spec('a',1.0)])
        self.wait_started('a')
        with self.s.db() as db:pid=db.execute("SELECT value FROM settings WHERE key='daemon_pid'").fetchone()[0]
        os.kill(pid,signal.SIGKILL)
        # Direct DB reads deliberately do not call kick().
        until=time.monotonic()+8
        status=None
        while time.monotonic()<until:
            with self.s.db() as db:status=db.execute('SELECT status FROM tasks WHERE team_id=?',(team,)).fetchone()[0]
            if status=='done':break
            time.sleep(.1)
        self.assertEqual(status,'done')
        self.assertEqual(json.loads((self.work/'a.run.json').read_text())['count'],1)


    def test_acceptance_success_runs_commands_once(self):
        (self.work/'source.py').write_text('source')
        command=[sys.executable,'-c',"from pathlib import Path; p=Path('checks.count'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"]
        team=self.create([self.spec('a',acceptance={'criteria':['checked'],'paths':['source.py'],'commands':[command]})])
        result=self.wait(team)
        self.assertEqual(result['state'],'done')
        self.assertTrue(result['tasks'][0]['result']['acceptance']['passed'])
        self.assertEqual((self.work/'checks.count').read_text(),'1')

    def test_watchdog_replaces_frozen_scheduler_without_replaying_worker(self):
        from worker import save
        team=self.create([self.spec('a',1.0)])
        self.wait_started('a')
        with self.s.db() as db:pid=db.execute("SELECT value FROM settings WHERE key='daemon_pid'").fetchone()[0]
        os.kill(pid,signal.SIGSTOP)
        save(self.api.root/'scheduler-heartbeat.json',{'pid':pid,'at':time.time()-60})
        until=time.monotonic()+10;status=None
        while time.monotonic()<until:
            with self.s.db() as db:status=db.execute('SELECT status FROM tasks WHERE team_id=?',(team,)).fetchone()[0]
            if status=='done':break
            time.sleep(.1)
        self.assertEqual(status,'done')
        self.assertEqual(json.loads((self.work/'a.run.json').read_text())['count'],1)


for name in dir(fixtures.RuntimeTests):
    if name.startswith('test_') and name not in ReliabilityRuntimeTests.__dict__:
        setattr(ReliabilityRuntimeTests,name,None)
