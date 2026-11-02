import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
import test_scheduler as fixtures


class ReliabilityTests(fixtures.SchedulerTests):
    # Inherit the fixture, not the existing test methods.
    def test_new_team_gets_next_free_slot(self):
        a = self.scheduler.create_team({'tasks':[self.spec(n) for n in 'abc']}, start=False)
        first, second = self.scheduler.claim_next(), self.scheduler.claim_next()
        b = self.scheduler.create_team({'tasks':[self.spec('small')]}, start=False)
        self.scheduler.finish(first, 'done', {})
        self.assertEqual(self.scheduler.claim_next()['team_id'], b['team_id'])
        self.assertEqual(self.scheduler.task(second['agent_id'])['status'], 'running')

    def test_second_failure_requires_parent_override(self):
        team = self.scheduler.create_team({'tasks':[self.spec('a')]}, start=False)
        aid = team['tasks'][0]['agent_id']
        with patch('scheduler.Scheduler.kick'):
            self.scheduler.finish(self.scheduler.claim_next(), 'failed', {'summary':'bad'})
            self.scheduler.resume(aid, 'repair')
            self.scheduler.finish(self.scheduler.claim_next(), 'failed', {'summary':'bad'})
            with self.assertRaisesRegex(ValueError, 'circuit'):
                self.scheduler.resume(aid, 'try forever')
        self.assertEqual(self.scheduler.inspect_team(team['team_id'])['tasks'][0]['status'], 'escalated')

    def test_stale_revision_does_not_queue_a_turn(self):
        team = self.scheduler.create_team({'tasks':[self.spec('a')]}, start=False)
        aid = team['tasks'][0]['agent_id']
        self.scheduler.finish(self.scheduler.claim_next(), 'done', {})
        with patch('scheduler.Scheduler.kick'):
            self.scheduler.resume(aid, 'next', expected_revision=1)
            with self.assertRaisesRegex(ValueError, 'revision'):
                self.scheduler.resume(aid, 'stale', expected_revision=1)
        self.assertEqual(self.scheduler.task(aid)['revision'], 2)

    def test_handoff_transmits_decisions_and_open_issues(self):
        team = self.scheduler.create_team({'tasks':[self.spec('a'),self.spec('b',depends_on=['a'])]}, start=False)
        a = self.scheduler.claim_next()
        self.scheduler.finish(a, 'done', {'summary':'implemented','rationale':'stable interface','checks':'unit passed','open_issues':'integration pending'})
        b = self.scheduler.claim_next()
        handoff = json.loads(b['inputs'])[0]
        self.assertEqual(handoff.get('rationale'), 'stable interface')
        self.assertEqual(handoff.get('open_issues'), 'integration pending')

    def test_peer_storm_is_bounded(self):
        team = self.scheduler.create_team({'tasks':[self.spec('a',limits={'max_peer_messages':2}), self.spec('b')]}, start=False)
        a = self.scheduler.task(team['tasks'][0]['agent_id'])
        for n in range(5):
            self.scheduler.ingest(a, {'id':str(n),'kind':'peer','event_type':'artifact_ready','target':'b','message':'ready'})
        box = self.base/'registry'/team['tasks'][1]['agent_id']/'mailbox/inbox'
        self.assertEqual(len(list(box.glob('*.json'))), 2)

    def test_final_review_rejects_stale_task_revision(self):
        team = self.scheduler.create_team({'tasks':[self.spec('a')]}, start=False)
        aid = team['tasks'][0]['agent_id']
        self.scheduler.finish(self.scheduler.claim_next(), 'done', {})
        self.scheduler.review_team(team['team_id'], {'a':1}, 'Reviewed final diff and acceptance results')
        self.assertEqual(self.scheduler.inspect_team(team['team_id'])['review']['status'], 'accepted')
        with patch('scheduler.Scheduler.kick'):
            self.scheduler.resume(aid, 'fix another issue', expected_revision=1)
        self.assertEqual(self.scheduler.inspect_team(team['team_id'])['review']['status'], 'pending')
        with self.assertRaises(ValueError):
            self.scheduler.review_team(team['team_id'], {'a':1}, 'old result')


    def test_parent_delivery_rejects_changed_tested_source(self):
        from reliability import fingerprint
        source=self.workspace/'source.py';source.write_text('one')
        team=self.scheduler.create_team({'tasks':[self.spec('a',acceptance={'criteria':['passes'],'commands':[['true']],'paths':['source.py']})]},start=False)
        task=self.scheduler.claim_next()
        self.scheduler.finish(task,'done',{'acceptance':{'passed':True,'fingerprint':fingerprint(self.workspace,['source.py'])}})
        source.write_text('two')
        with self.assertRaisesRegex(ValueError,'source'):
            self.scheduler.review_team(team['team_id'],{'a':1},'Review old source')

    def test_waiting_for_parent_is_not_mistaken_for_a_stuck_tool(self):
        from reliability import stalled, limits
        from worker import save
        root=self.base/'parent-wait';(root/'mailbox/questions').mkdir(parents=True)
        save(root/'mailbox/questions/q.json',{'invocation':'current','status':'pending','at':995000})
        state={'active':{'result_file':'current'},'heartbeat_at':999,'progress_at':900,'tool_started_at':900}
        self.assertIsNone(stalled(root,state,limits({'tool_timeout_seconds':10}),now=1000))
        state['heartbeat_at']=1199
        self.assertIn('Tool',stalled(root,state,limits({'tool_timeout_seconds':10}),now=1200))


# Only run new reliability cases; keep shared setup/helpers without duplicating the old suite.
for name in dir(fixtures.SchedulerTests):
    if name.startswith('test_') and name not in ReliabilityTests.__dict__:
        setattr(ReliabilityTests, name, None)
