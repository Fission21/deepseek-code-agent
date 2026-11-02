"""Fresh requirement review; the parent retains the delivery decision."""
import json
from pathlib import Path

from reliability import fingerprint
from worker import read_state


def expand_review_tasks(params):
    mode=params.get('review_policy','legacy')
    if mode not in ('legacy','independent','light'): raise ValueError('Unknown review policy')
    reason=params.get('review_reason','')
    if mode=='light' and (not isinstance(reason,str) or not reason.strip()):
        raise ValueError('Light review requires a risk-based reason')
    specs=[dict(s,delivery_review={'mode':mode,'reason':reason}) for s in params['tasks']]
    if mode!='independent': return specs
    groups={}
    for s in specs:
        if s['id'].startswith('_review_'): raise ValueError('Reserved reviewer task ID')
        groups.setdefault(str(Path(s['workspace']).expanduser().resolve()),[]).append(s)
    for n,(workspace,targets) in enumerate(groups.items(),1):
        requirements=[];paths=[]
        for s in targets:
            criteria=(s.get('acceptance') or {}).get('criteria',[s['task']])
            requirements.append({'task_id':s['id'],'original_requirement':s['task'],
                'criteria':[{'id':s['id']+'#'+str(i),'text':c} for i,c in enumerate(criteria,1)]})
            paths+=s.get('scope_paths',[])+(s.get('acceptance') or {}).get('paths',[])+s.get('outputs',[])
        prompt=('Independently review the original requirements against the current source and delivered evidence. '
                'You are a fresh reviewer, not an implementer. Do not edit files. Do not rerun valid checks just for reporting. '
                'Test success alone does not prove requirement satisfaction. Report findings to the responsible task owner '
                'using send_peer; the parent arbitrates. Never repair their implementation yourself. '
                'For each criterion submit complete_task review_json as a JSON array of '
                '{criterion, verdict: pass|fail|unverified, evidence: concrete source/output reference and reasoning}. '
                'Missing evidence means unverified. Any fail/unverified means outcome blocked. '
                'Original requirements: '+json.dumps(requirements,ensure_ascii=False)+
                '\nShared contract: '+params.get('contract',''))
        extra={k:targets[0][k] for k in ('dsh','timeout_seconds','budget_pool') if k in targets[0]}
        specs.append(dict(extra,id='_review_'+str(n),workspace=workspace,task=prompt,
            scope_paths=[],depends_on=[s['id'] for s in targets],review_targets=[s['id'] for s in targets],
            review_criteria=[c['id'] for r in requirements for c in r['criteria']],review_paths=sorted(set(paths)),
            delivery_review={'mode':mode,'reason':''},acceptance={'criteria':['Every original criterion has independent supporting evidence']}))
    return specs


def validate_review(scheduler,task,result):
    spec=json.loads(task['spec'])
    if not spec.get('review_targets'): return
    checks=result.get('review')
    if not isinstance(checks,list) or any(not isinstance(c,dict) for c in checks):
        raise ValueError('Independent review is missing')
    ids=[c.get('criterion') for c in checks]
    if len(ids)!=len(set(ids)) or set(ids)!=set(spec['review_criteria']):
        raise ValueError('Independent review omitted or duplicated criteria')
    if any(c.get('verdict')!='pass' or not isinstance(c.get('evidence'),str) or not c['evidence'].strip() for c in checks):
        raise ValueError('Requirement review has unresolved findings; return them to the owner')
    session=read_state(scheduler.root/task['agent_id']).get('session_id')
    if not session: raise ValueError('Reviewer session identity missing')
    inputs=json.loads(task.get('inputs') or '[]')
    if len(inputs)!=len(spec['review_targets']): raise ValueError('Review dependency binding missing')
    with scheduler.db() as db:
        for item in inputs:
            dep=db.execute('SELECT * FROM tasks WHERE team_id=? AND name=?',(task['team_id'],item['task_id'])).fetchone()
            if not dep or dep['status']!='done' or dep['revision']!=item['revision']:
                raise ValueError('Reviewed task revision changed')
            if read_state(scheduler.root/dep['agent_id']).get('session_id')==session:
                raise ValueError('Reviewer must use a different session from the implementer')
            if item.get('review_fingerprint')!=fingerprint(spec['workspace'],spec['review_paths']):
                raise ValueError('Reviewed source changed after review started')


def validate_delivery(scheduler,rows):
    for task in rows:
        spec=json.loads(task['spec']);policy=spec.get('delivery_review',{})
        if policy.get('mode') not in ('independent','light'):
            raise ValueError('No requirement review policy; legacy work needs an explicit review decision')
        if policy['mode']=='light' and not policy.get('reason','').strip():
            raise ValueError('Light review reason missing')
        if spec.get('review_targets'):validate_review(scheduler,task,json.loads(task['result'] or '{}'))
        elif policy['mode']=='independent' and not any(task['name'] in json.loads(r['spec']).get('review_targets',[]) for r in rows):
            raise ValueError('Independent requirement reviewer missing')
