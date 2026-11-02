import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from support import SCRIPTS


class BrowserGateTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((SCRIPTS/'browser_gate.py').exists(), 'Browser gate is missing')
        from browser_gate import BrowserGate
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.calls=[];self.url='https://example.com/page'
        self.policy={'tab_id':7,'url':self.url,'invocation':'current','mailbox':str(self.root)}
        def upstream(method,params):
            if method=='tools/call' and params['name']=='tabs':
                return {'content':[{'type':'text','text':'共 2 个标签页（test）\n[7] * page — '+self.url+'\n[9] other — https://private.invalid/'}]}
            self.calls.append((method,params))
            return {'content':[{'type':'text','text':'ok'}]}
        self.gate=BrowserGate(self.policy,upstream)

    def call(self,name,args):
        return self.gate.dispatch('tools/call',{'name':name,'arguments':args})

    def test_read_only_default_and_no_cross_tab_forward(self):
        self.assertNotIn('isError',self.call('snapshot',{'tabId':7}))
        for name,args in [('click',{'tabId':7,'ref':'e1'}),('snapshot',{'tabId':9}),('eval',{'tabId':7,'expression':'1'}),('screenshot',{'tabId':7,'savePath':'/tmp/leak'})]:
            self.assertTrue(self.call(name,args)['isError'])
        self.assertEqual(len(self.calls),1)

    def test_grant_matches_exact_payload_and_is_single_use(self):
        args={'tabId':7,'ref':'e2'}
        result=self.call('click',args)
        request=json.loads(result['content'][0]['text'])['request_id']
        from browser_gate import grant
        grant(self.root,request,'current','Already authorized click',ttl=30)
        self.assertTrue(self.call('click',{'tabId':7,'ref':'e3'})['isError'])
        self.assertNotIn('isError',self.call('click',args))
        self.assertTrue(self.call('click',args)['isError'])
        self.assertEqual(len(self.calls),1)

    def test_navigation_and_expired_grant_block_without_forward(self):
        result=self.call('click',{'tabId':7,'ref':'e2'})
        request=json.loads(result['content'][0]['text'])['request_id']
        from browser_gate import grant
        grant(self.root,request,'current','Authorized',ttl=.01)
        time.sleep(.02)
        self.assertTrue(self.call('click',{'tabId':7,'ref':'e2'})['isError'])
        self.url='https://other.example/page'
        self.assertTrue(self.call('snapshot',{'tabId':7})['isError'])
        self.assertEqual(self.calls,[])

    def test_injected_title_lines_fail_closed(self):
        self.url='https://evil.invalid/\n[7] * spoof — https://example.com/page'
        self.assertTrue(self.call('snapshot',{'tabId':7})['isError'])
        self.assertEqual(self.calls,[])

    def test_stdio_proxy_uses_real_upstream_and_suppresses_unassigned_tabs(self):
        import subprocess,sys
        fake=self.root/'fake_mcp.py'
        fake.write_text("import sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n if 'id' not in r:continue\n p=r.get('params',{}); name=p.get('name')\n data={'content':[{'type':'text','text':'共 1 个标签页（test）\\n[7] * page — https://example.com/page' if name=='tabs' else 'snapshot only'}]}\n print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':data}),flush=True)\n",encoding='utf-8')
        policy=self.root/'policy.json';policy.write_text(json.dumps(self.policy))
        request={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'snapshot','arguments':{'tabId':7}}}
        result=subprocess.run([sys.executable,str(SCRIPTS/'browser_gate.py'),'--policy',str(policy),sys.executable,str(fake)],input=json.dumps(request)+'\n',capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        reply=json.loads(result.stdout)
        self.assertEqual(reply['result']['content'][0]['text'],'snapshot only')
        self.assertNotIn('个标签页',result.stdout)

    def test_authorization_request_does_not_store_editable_or_locator_values(self):
        secret='SECRET-INPUT-DO-NOT-PERSIST'
        result=self.call('fill',{'tabId':7,'fields':[{'ref':'e1','text':secret}], 'selector':'input[value="'+secret+'"]'})
        self.assertTrue(result['isError'])
        requests=list((self.root/'browser').glob('*.json'))
        self.assertEqual(len(requests),1)
        record=json.loads(requests[0].read_text())
        self.assertNotIn(secret,requests[0].read_text())
        self.assertNotIn(secret,json.dumps(result))
        self.assertEqual(record['review']['tool'],'fill')
        self.assertEqual(len(record['arguments_sha256']),64)

    def test_malformed_params_do_not_end_the_proxy_session(self):
        for bad in (None,[],17,'invalid'):
            result=self.gate.dispatch('tools/call',bad)
            self.assertTrue(result['isError'])
        self.assertNotIn('isError',self.call('snapshot',{'tabId':7}))

    def test_upstream_non_object_messages_are_ignored_and_bad_results_are_errors(self):
        import sys
        from browser_gate import Upstream
        fake=self.root/'noisy_mcp.py'
        fake.write_text("import json,sys\nn=0\nfor line in sys.stdin:\n r=json.loads(line); n+=1\n print('null',flush=True)\n print('[]',flush=True)\n print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':[] if n==1 else {'ok':True}}),flush=True)\n",encoding='utf-8')
        upstream=Upstream([sys.executable,str(fake)])
        try:
            with self.assertRaises(ValueError):upstream.call('ping',{})
            self.assertEqual(upstream.call('ping',{}),{'ok':True})
        finally:upstream.close()
