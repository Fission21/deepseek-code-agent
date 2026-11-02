import time
import unittest
import test_scheduler


@test_scheduler.POSIX_ONLY
class AttentionTests(unittest.TestCase):
    setUp=test_scheduler.SchedulerTests.setUp
    tearDown=test_scheduler.SchedulerTests.tearDown
    spec=test_scheduler.SchedulerTests.spec

    def test_observation_discovers_unmanaged_legacy_session_without_starting_it(self):
        from agent_api import AgentAPI
        from attention import observe
        api=AgentAPI(self.scheduler.root)
        agent=api._raw_call('spawn_agent',dict(self.spec('old'),_defer=True))
        before=(self.scheduler.root/agent['agent_id']/'state.json').read_text()
        result=observe(self.scheduler,{})
        self.assertEqual([a['agent_id'] for a in result['unmanaged_agents']],[agent['agent_id']])
        self.assertEqual((self.scheduler.root/agent['agent_id']/'state.json').read_text(),before)

    def test_progress_stays_silent_until_timeout_and_cursor_advances(self):
        from attention import observe
        t=self.scheduler.create_team({'tasks':[self.spec('a')]},start=False)
        with self.scheduler.db(True) as db:
            for n in range(240):self.scheduler.emit(db,t['team_id'],'a','progress',{'message':'working'})
        start=time.monotonic()
        r=observe(self.scheduler,{'team_id':t['team_id'],'timeout_seconds':.2})
        self.assertGreaterEqual(time.monotonic()-start,.18)
        self.assertEqual(r['attention'],[])
        self.assertGreaterEqual(r['cursor']['seq'],241)
        self.assertTrue(r['timed_out']);self.assertFalse(r['host_push_available'])

    def test_question_and_budget_block_wake_and_terminal_is_delivered_once(self):
        from attention import observe
        t=self.scheduler.create_team({'tasks':[self.spec('a')]},start=False)
        with self.scheduler.db(True) as db:self.scheduler.emit(db,t['team_id'],'a','question',{'request_id':'q','message':'Need contract'})
        r=observe(self.scheduler,{'team_id':t['team_id'],'timeout_seconds':.3})
        self.assertEqual([e['kind'] for e in r['attention']],['question'])
        task=self.scheduler.claim_next();self.scheduler.finish(task,'done',{})
        r=observe(self.scheduler,{'team_id':t['team_id'],'cursor':r['cursor']})
        self.assertEqual([e['kind'] for e in r['attention']],['team_done'])
        again=observe(self.scheduler,{'team_id':t['team_id'],'cursor':r['cursor']})
        self.assertEqual(again['attention'],[])

    def test_queued_peer_question_rejected_but_artifact_delivery_allowed(self):
        team=self.scheduler.create_team({'tasks':[self.spec('a'),self.spec('b')]},start=False)
        task=self.scheduler.claim_next()
        for kind in ('question','artifact_ready'):
            self.scheduler.ingest(task,{'id':kind,'kind':'peer','target':'b','event_type':kind,'correlation_id':'q','message':'detail'})
        events=self.scheduler.inspect_team(team['team_id'])['events']
        self.assertEqual([e['kind'] for e in events[-2:]],['peer_rejected','peer'])
