"""Assigned-tab MCP gate. Cooperative tool policy, not a same-user OS sandbox."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from worker import save

READ = {'snapshot','read_text','query','screenshot','scroll','wait','learnings'}
WRITE = {'click','type','select','fill','key','navigate','act'}


def origin(url):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('Only credential-free http(s) origins are supported')
    return (p.scheme,p.hostname,p.port or (443 if p.scheme=='https' else 80))


def grant(mailbox, request_id, invocation, reason, ttl=60):
    if not re.fullmatch(r'b-[0-9a-f]{64}',request_id):
        raise ValueError('Invalid browser request ID')
    if not isinstance(reason,str) or not reason.strip():
        raise ValueError('Record the existing authorization for this action')
    root=Path(mailbox)/'browser';request=root/(request_id+'.json')
    data=json.loads(request.read_text())
    if data['invocation']!=invocation or time.time()-data['created']>600:
        raise ValueError('Browser request is stale')
    if (root/(request_id+'.used')).exists():
        raise ValueError('This browser action was already consumed; do not replay it')
    save(root/(request_id+'.grant'),{'invocation':invocation,'expires':time.time()+min(60,ttl),'reason':reason})
    return {'request_id':request_id,'granted':True,'single_use':True}


class BrowserGate:
    def __init__(self, policy, upstream):
        self.policy=policy;self.upstream=upstream
        self.root=Path(policy['mailbox'])/'browser'
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.allowed_origin=origin(policy['url'])

    def current_page(self):
        # huashu 1.3.0 formats tabs as a count plus exactly one line per tab.
        # Reject embedded newlines / ambiguous IDs rather than trusting page titles.
        result=self.upstream('tools/call',{'name':'tabs','arguments':{'action':'list'}})
        if result.get('isError'):
            raise ValueError('Cannot establish assigned tab identity')
        texts=[x['text'] for x in result.get('content',[]) if x.get('type')=='text']
        if len(texts)!=1:
            raise ValueError('Unsupported tab metadata format')
        lines=texts[0].split('\n');count=re.match(r'^共 (\d+) 个标签页',lines[0])
        if not count or len(lines)!=int(count[1])+1:
            raise ValueError('Ambiguous tab metadata; no action forwarded')
        tabs={}
        for line in lines[1:]:
            match=re.match(r'^\[(\d+)\]',line)
            if not match or ' — ' not in line or int(match[1]) in tabs:
                raise ValueError('Ambiguous tab metadata')
            tabs[int(match[1])]=line.rsplit(' — ',1)[1]
        url=tabs.get(self.policy['tab_id'])
        if not url or origin(url)!=self.allowed_origin:
            raise ValueError('Assigned tab is missing or changed origin')
        return url

    def validate(self, name, args):
        if name not in READ|WRITE or not isinstance(args,dict):
            raise ValueError('Tool is unavailable under the browser policy')
        if name=='learnings':
            if set(args)-{'domain'} or args.get('domain')!=urlsplit(self.policy['url']).hostname:
                raise ValueError('Only assigned-domain learning reads are allowed')
            return
        if isinstance(args.get('tabId'),bool) or args.get('tabId')!=self.policy['tab_id']:
            raise ValueError('Every browser call must target the assigned tab')
        if args.get('savePath') or args.get('focus'):
            raise ValueError('File export and browser focus changes are unavailable')
        def walk(value):
            if isinstance(value,dict):
                if 'tabId' in value and value['tabId']!=self.policy['tab_id']:
                    raise ValueError('Nested cross-tab action is unavailable')
                if 'url' in value and origin(value['url'])!=self.allowed_origin:
                    raise ValueError('Cross-origin navigation is unavailable')
                if 'do' in value and value['do'] not in WRITE|{'wait','scroll','read','repeat','if','assert'}:
                    raise ValueError('Unsupported nested action')
                for child in value.values():walk(child)
            elif isinstance(value,list):
                for child in value:walk(child)
        walk(args)
        if name=='navigate' and not args.get('url'):
            raise ValueError('Navigation needs an explicit same-origin target')

    def dispatch(self, method, params):
        try:
            if method in ('initialize','ping','notifications/initialized'):
                return self.upstream(method,params)
            if method=='tools/list':
                data=self.upstream(method,params)
                data['tools']=[t for t in data.get('tools',[]) if t.get('name') in READ|WRITE]
                return data
            if method!='tools/call':
                raise ValueError('Unsupported MCP method')
            name=params.get('name');args=params.get('arguments',{})
            self.validate(name,args)
            page=self.current_page()
            if name in WRITE:
                body={'name':name,'arguments':args,'invocation':self.policy['invocation'],
                      'page_sha256':hashlib.sha256(page.encode()).hexdigest()}
                request_id='b-'+hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
                request=self.root/(request_id+'.json');permit=self.root/(request_id+'.grant');used=self.root/(request_id+'.used')
                if not request.exists():
                    save(request,dict(body,id=request_id,created=time.time()))
                authorized=False
                if permit.exists() and not used.exists():
                    data=json.loads(permit.read_text())
                    authorized=data.get('invocation')==self.policy['invocation'] and data.get('expires',0)>time.time()
                if not authorized:
                    return {'isError':True,'content':[{'type':'text','text':json.dumps({
                        'blocked':'Use ask_parent with this request ID and wait. Parent must authorize_browser_action, then reply_agent. Do not retry or bypass with shell.',
                        'request_id':request_id})}]}
                # Consume before forwarding: timeouts cannot make a write replayable.
                os.replace(permit,used)
            result=self.upstream(method,params)
            self.current_page()  # suppress page data after an unexpected origin change
            return result
        except (ValueError,KeyError,OSError,TypeError) as exc:
            return {'isError':True,'content':[{'type':'text','text':str(exc)}]}


class Upstream:
    def __init__(self, command):
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        self.responses=queue.Queue();self.sequence=0
        def read():
            for line in self.process.stdout:
                try:self.responses.put(json.loads(line))
                except ValueError:pass
            self.responses.put(None)
        threading.Thread(target=read,daemon=True).start()

    def call(self, method, params):
        self.sequence+=1;number=self.sequence
        request={'jsonrpc':'2.0','method':method,'params':params}
        if not method.startswith('notifications/'):
            request['id']=number
        self.process.stdin.write(json.dumps(request)+'\n');self.process.stdin.flush()
        if 'id' not in request:return {}
        end=time.monotonic()+55
        while time.monotonic()<end:
            try:response=self.responses.get(timeout=max(.01,end-time.monotonic()))
            except queue.Empty:raise ValueError('Browser tool deadline expired; do not replay uncertain writes') from None
            if response is None:raise ValueError('Browser MCP disconnected; do not replay uncertain writes')
            if response.get('id')==number:
                if 'error' in response:raise ValueError('Browser MCP request failed')
                return response.get('result',{})
        raise ValueError('Browser MCP deadline expired')

    def close(self):
        self.process.terminate()
        try:self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.process.stdin.close();self.process.stdout.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy',required=True,type=Path)
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args();up=Upstream(args.command)
    gate=BrowserGate(json.loads(args.policy.read_text()),up.call)
    try:
        for line in sys.stdin:
            req={}
            try:
                req=json.loads(line)
                if not isinstance(req,dict):raise ValueError('JSON-RPC object required')
                result=gate.dispatch(req['method'],req.get('params',{}))
                response={'jsonrpc':'2.0','id':req.get('id'),'result':result}
            except (ValueError,KeyError,TypeError):
                response={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid JSON-RPC request'}}
            if not isinstance(req,dict) or 'id' in req or 'error' in response:
                print(json.dumps(response),flush=True)
    finally:up.close()


if __name__=='__main__':main()
