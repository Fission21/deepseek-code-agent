import concurrent.futures
import tempfile
import time
import unittest
from pathlib import Path

from support import SCRIPTS
from budget import BudgetLedger


def policy(**changes):
    return dict(pool_id='fixture', limit_micros=1500, provider='deepseek-official',
                model='deepseek-flash', input_price_micros=1000000,
                output_price_micros=2000000, max_input_tokens=100,
                max_output_tokens=50, attempt_bound=2, expires_at=time.time()+3600, **changes)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.b=BudgetLedger(self.root)
        self.b.configure(policy(), default=True)

    def reserve(self, key):
        return self.b.reserve('fixture', key, 'worker', 'deepseek-official', 'deepseek-flash', 100, 50)

    def test_eight_competing_requests_cannot_overspend(self):
        def claim(i):
            try: return self.reserve(str(i))
            except ValueError: return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            accepted=list(ex.map(claim, range(8)))
        self.assertEqual(sum(x is not None for x in accepted), 3)
        self.assertEqual(self.b.snapshot('fixture')['available_micros'], 300)

    def test_crash_keeps_reservation_and_replay_cannot_dispatch_twice(self):
        self.reserve('a')
        reopened=BudgetLedger(self.root)
        self.assertEqual(reopened.snapshot('fixture')['reserved_micros'], 400)
        with self.assertRaises(ValueError): self.reserve('a')

    def test_settlement_is_idempotent_and_retains_hidden_attempt_ceiling(self):
        self.reserve('a')
        usage={'inputTokens':10,'cacheReadTokens':5,'outputTokens':7}
        first=self.b.settle('a', usage)
        self.assertEqual(first['charged_micros'], 229)
        self.assertEqual(self.b.settle('a',usage), first)
        self.assertEqual(self.b.snapshot('fixture')['available_micros'], 1271)
        with self.assertRaises(ValueError): self.b.settle('a',{'inputTokens':1,'outputTokens':1})

    def test_missing_route_expired_policy_and_oversized_request_reject(self):
        with self.assertRaises(ValueError): BudgetLedger(self.root/'empty').binding()
        for route,inp,out in [('other',100,50),('deepseek-flash',101,50),('deepseek-flash',100,51)]:
            with self.assertRaises(ValueError): self.b.reserve('fixture',route,'w','deepseek-official',route,inp,out)
        with self.b.db(True) as db: db.execute("UPDATE pools SET expires_at=0")
        with self.assertRaises(ValueError): self.reserve('expired')
        self.assertEqual(self.b.snapshot('fixture')['reserved_micros'], 0)

    def test_invalid_usage_never_refunds_and_pool_cannot_be_reset(self):
        self.reserve('a')
        for usage in [None, {}, {'inputTokens':-1,'outputTokens':0}, {'inputTokens':101,'outputTokens':0}]:
            with self.assertRaises(ValueError): self.b.settle('a',usage)
        self.assertEqual(self.b.snapshot('fixture')['reserved_micros'],400)
        with self.assertRaises(ValueError): self.b.configure(policy(), default=True)

    def test_text_request_refunds_unused_bound_without_opaque_media_retry(self):
        self.b.reserve('fixture','text','worker','deepseek-official','deepseek-flash',100,50,False)
        self.assertEqual(self.b.snapshot('fixture')['reserved_micros'],200)
        self.assertEqual(self.b.settle('text',{'inputTokens':10,'outputTokens':7})['charged_micros'],24)

    def test_refresh_keeps_total_spend_and_original_request_prices(self):
        self.b.reserve('fixture','old','w','deepseek-official','deepseek-flash',100,50,False)
        self.b.refresh_prices('fixture',2000000,4000000,time.time()+7200)
        self.assertEqual(self.b.settle('old',{'inputTokens':10,'outputTokens':7})['charged_micros'],24)
        self.b.reserve('fixture','new','w','deepseek-official','deepseek-flash',100,50,False)
        self.assertEqual(self.b.settle('new',{'inputTokens':10,'outputTokens':7})['charged_micros'],48)
        snap=self.b.snapshot('fixture')
        self.assertEqual(snap['limit_micros'],1500)
        self.assertEqual(snap['charged_micros'],72)

    def test_scheduler_denies_unfunded_team_before_creating_worker(self):
        import os
        if os.name!='posix': self.skipTest('POSIX scheduler')
        from scheduler import Scheduler
        from support import install_fake
        work=self.root/'work';work.mkdir()
        fake=install_fake(self.root/'fake')
        scheduler=Scheduler(self.root/'unfunded')
        spec={'id':'a','task':'fixture','workspace':str(work),'dsh':str(fake)}
        with self.assertRaisesRegex(ValueError,'budget'):
            scheduler.create_team({'tasks':[spec]},start=False)
        self.assertEqual(list(scheduler.root.glob('dsh-*')),[])

    def test_scheduler_stops_dispatch_when_request_budget_is_reserved_elsewhere(self):
        import os
        if os.name!='posix': self.skipTest('POSIX scheduler')
        from scheduler import Scheduler
        from support import install_fake
        work=self.root/'work';work.mkdir();fake=install_fake(self.root/'fake')
        scheduler=Scheduler(self.root)
        t=scheduler.create_team({'tasks':[{'id':'a','task':'fixture','workspace':str(work),'dsh':str(fake)}]},start=False)
        for n in range(3):self.reserve(str(n))
        self.assertIsNone(scheduler.claim_next())
        self.assertEqual(scheduler.inspect_team(t['team_id'])['tasks'][0]['status'],'blocked_budget')
