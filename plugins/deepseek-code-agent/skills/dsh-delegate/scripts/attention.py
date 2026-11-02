"""Decision-only observations. Long-poll is not host push or an idle-chat wakeup."""
import hashlib
import json
import math
import time

DECISIONS={'question','blocked','failed','escalated','blocked_budget','blocked_version',
           'browser_request','peer_rejected','interrupted_uncertain','dispute'}


def observe(scheduler,params):
    if not params.get('team_id'):
        from agent_api import AgentAPI
        sessions=AgentAPI(scheduler.root)._raw_call('list_agents',{})['agents']
        with scheduler.db() as db:
            managed={r[0] for r in db.execute('SELECT agent_id FROM tasks')}
            return {'teams':[dict(r) for r in db.execute('SELECT id AS team_id,title,state FROM teams ORDER BY created DESC')],
                    'unmanaged_agents':[a for a in sessions if a['agent_id'] not in managed],
                    'delivery_mode':'long_poll','host_push_available':False}
    cursor=params.get('cursor') or {}
    if not isinstance(cursor,dict) or set(cursor)-{'seq','terminal'}:raise ValueError('Invalid observation cursor')
    seq=cursor.get('seq',0)
    if type(seq) is not int or seq<0:raise ValueError('Invalid event cursor')
    timeout=float(params.get('timeout_seconds',0))
    if not math.isfinite(timeout) or not 0<=timeout<=55:raise ValueError('Wait must be between 0 and 55 seconds')
    end=time.monotonic()+timeout
    while True:
        r=scheduler.inspect_team(params['team_id'],seq)
        attention=[e for e in r['events'] if e['kind'] in DECISIONS or
                   (e['kind']=='peer' and e['data'].get('event_type')=='blocked')]
        seq=r['cursor']
        terminal=hashlib.sha256(json.dumps([r['state'],[(t['name'],t['revision'],t['status']) for t in r['tasks']]]).encode()).hexdigest() if r['state']!='running' else None
        if terminal and terminal!=cursor.get('terminal'):
            attention.append({'kind':'team_'+r['state'],'data':{'state':r['state']}})
        if attention or time.monotonic()>=end:
            return {'team_id':r['team_id'],'state':r['state'],'attention':attention,
                    'tasks':[{k:t[k] for k in ('name','agent_id','status','revision','error')} for t in r['tasks']],
                    **({'results':{t['name']:t['result'] for t in r['tasks']},'review':r['review']} if terminal and terminal!=cursor.get('terminal') else {}),
                    'cursor':{'seq':seq,'terminal':terminal},'timed_out':not bool(attention),
                    'delivery_mode':'long_poll','host_push_available':False}
        # Consume a backlog without sleeping or treating it as a reason to wake a model.
        if len(r['events'])<200:time.sleep(min(.1,max(0,end-time.monotonic())))
