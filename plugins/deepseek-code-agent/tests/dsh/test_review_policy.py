import json
from unittest.mock import patch
import test_scheduler
import unittest


@test_scheduler.POSIX_ONLY
class ReviewPolicyTests(unittest.TestCase):
    setUp=test_scheduler.SchedulerTests.setUp
    tearDown=test_scheduler.SchedulerTests.tearDown
    spec=test_scheduler.SchedulerTests.spec

    def test_reviewer_receives_original_attachments_and_required_rules(self):
        from review_policy import expand_review_tasks
        tasks=expand_review_tasks({'review_policy':'independent','tasks':[
            self.spec('a',context_files=['requirements.md'],required_reads=['rules.md']),
            self.spec('b',context_files=['requirements.md','contract.md'],required_reads=['rules.md'])]})
        reviewer=tasks[-1]
        self.assertEqual(reviewer['context_files'],['requirements.md','contract.md'])
        self.assertEqual(reviewer['required_reads'],['rules.md'])

    def test_legacy_delivery_can_record_reasoned_light_review_without_rerunning(self):
        team=self.scheduler.create_team({'tasks':[self.spec('a')]},start=False)
        task=self.scheduler.claim_next();self.scheduler.finish(task,'done',{'summary':'old delivered result'})
        with self.scheduler.db(True) as db:
            spec=json.loads(task['spec']);spec.pop('delivery_review')
            db.execute('UPDATE tasks SET spec=? WHERE agent_id=?',(json.dumps(spec),task['agent_id']))
        with self.assertRaises(ValueError):self.scheduler.review_team(team['team_id'],{'a':1},'original result')
        result=self.scheduler.review_team(team['team_id'],{'a':1},'Parent reviewed requirement evidence',legacy_review_reason='Read-only legacy investigation; parent inspected supplied sources')
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(self.scheduler.task(task['agent_id'])['attempt'],1)
        self.assertIn('legacy_review_reason',result)
    def test_light_review_requires_reason_and_unreviewed_legacy_cannot_deliver(self):
        with self.assertRaisesRegex(ValueError,'reason'):
            self.scheduler.create_team({'review_policy':'light','tasks':[self.spec('a')]},start=False)
        team=self.scheduler.create_team({'tasks':[self.spec('a')]},start=False)
        task=self.scheduler.claim_next();self.scheduler.finish(task,'done',{})
        with self.assertRaisesRegex(ValueError,'review'):
            self.scheduler.review_team(team['team_id'],{'a':1},'looks done')

    def test_independent_review_uses_requirements_not_author_history(self):
        team=self.scheduler.create_team({'review_policy':'independent','tasks':[self.spec('a',scope_paths=['code.txt'],acceptance={'criteria':['Must show user status']})]},start=False)
        self.assertEqual(len(team['tasks']),2)
        author=self.scheduler.claim_next()
        (self.workspace/'code.txt').write_text('v1')
        self.scheduler.finish(author,'done',{'summary':'BIASED_SUCCESS_NARRATIVE'})
        reviewer=self.scheduler.claim_next();spec=json.loads(reviewer['spec'])
        self.assertEqual(spec['scope_paths'],[])
        self.assertIn('Must show user status',spec['task'])
        self.assertNotIn('BIASED_SUCCESS_NARRATIVE',reviewer['inputs'])
        self.assertNotEqual(author['agent_id'],reviewer['agent_id'])
        self.assertEqual(spec['review_targets'],['a'])

    def test_review_rejects_missing_criteria_self_session_and_stale_source(self):
        from review_policy import validate_review
        team=self.scheduler.create_team({'review_policy':'independent','tasks':[self.spec('a',scope_paths=['code.txt'],acceptance={'criteria':['Status visible','Failures recover']})]},start=False)
        author=self.scheduler.claim_next();(self.workspace/'code.txt').write_text('v1')
        self.scheduler.finish(author,'done',{})
        reviewer=self.scheduler.claim_next()
        from worker import locked
        for task,session in [(author,'implementation-session'),(reviewer,'review-session')]:
            with locked(self.scheduler.root/task['agent_id']) as state:state['session_id']=session
        checks=[{'criterion':'a#1','verdict':'pass','evidence':'code.txt:1 handles visible status'},
                {'criterion':'a#2','verdict':'pass','evidence':'code.txt:2 handles retry'}]
        valid={'review':checks}
        validate_review(self.scheduler,reviewer,valid)
        with self.assertRaises(ValueError):validate_review(self.scheduler,reviewer,{'review':checks[:1]})
        with locked(self.scheduler.root/reviewer['agent_id']) as state:state['session_id']='implementation-session'
        with self.assertRaisesRegex(ValueError,'session'):validate_review(self.scheduler,reviewer,valid)
        with locked(self.scheduler.root/reviewer['agent_id']) as state:state['session_id']='review-session'
        (self.workspace/'code.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError,'changed'):validate_review(self.scheduler,reviewer,valid)
