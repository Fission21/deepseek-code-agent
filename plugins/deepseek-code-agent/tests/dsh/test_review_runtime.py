import json
import unittest
import test_team_runtime as fixtures


@fixtures.unittest.skipUnless(fixtures.os.name=='posix','POSIX execution')
class ReviewRuntimeTests(unittest.TestCase):
    setUp=fixtures.RuntimeTests.setUp
    tearDown=fixtures.RuntimeTests.tearDown
    spec=fixtures.RuntimeTests.spec
    wait=fixtures.RuntimeTests.wait

    def test_submit_runs_fresh_reviewer_and_binds_final_delivery_to_reviewed_source(self):
        result=self.api.call('submit_work',{'tasks':[self.spec('author',scope_paths=['source.txt'],
            fixture={'files':{'source.txt':'delivered'}},acceptance={'criteria':['source delivered']})]})
        team=result['team_id'];self.teams.append(team)
        done=self.wait(team)
        self.assertEqual(done['state'],'done')
        ids={t['name']:t['revision'] for t in done['tasks']}
        author=json.loads((self.work/'author.run.json').read_text())
        reviewer=json.loads((self.work/'_review_1.run.json').read_text())
        self.assertLess(author['end'],reviewer['start'])
        self.assertNotEqual(author['session'],reviewer['session'])
        self.assertEqual(self.api.call('deliver_work',{'team_id':team,'revisions':ids,'summary':'Reviewed fixture'})['status'],'accepted')
        (self.work/'source.txt').write_text('changed later')
        with self.assertRaisesRegex(ValueError,'changed'):
            self.api.call('deliver_work',{'team_id':team,'revisions':ids,'summary':'Old evidence'})
