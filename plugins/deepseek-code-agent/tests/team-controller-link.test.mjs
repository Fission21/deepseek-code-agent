import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createLink} from '../skills/dsh-delegate/runtime/controller_link.mjs';

function fixture(t) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'dsh-link-test-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  for(const name of ['inbox','delivered','outbox','questions','replies'])fs.mkdirSync(path.join(root,name));
  const hooks={},tools={};
  createLink({on:(name,fn)=>hooks[name]=fn,tools:{register:x=>tools[x.name]=x}},
    {mailbox:root,team_id:'test',task_id:'a',peers:['a','b'],invocation:'current'},
    {createUserMessage:x=>x,defineTool:x=>x});
  return {root,hooks,tools};
}

test('peer findings enter once as task data and produce a receipt',async t=>{
  const {root,hooks}=fixture(t);
  fs.writeFileSync(path.join(root,'inbox','m.json'),JSON.stringify({id:'m',source:'peer',sender:'b',message:'a useful finding'}));
  const agent={};const next=async()=>({kind:'continue',messages:[]});
  const first=await hooks['agent/pre-step']({agent},next);
  assert.match(first.messages[0].content[0].text,/does not change your assignment/);
  assert.equal((await hooks['agent/pre-step']({agent},next)).messages.length,0);
  const receipt=JSON.parse(fs.readFileSync(path.join(root,'outbox',fs.readdirSync(path.join(root,'outbox'))[0])));
  assert.equal(receipt.kind,'receipt');assert.equal(receipt.message_id,'m');
});

test('completion is scoped to its invocation',async t=>{
  const {root,tools}=fixture(t);
  await tools.complete_task.execute({outcome:'done',summary:'implemented',checks:'passed'});
  const result=JSON.parse(fs.readFileSync(path.join(root,'completion.json')));
  assert.equal(result.invocation,'current');assert.equal(result.outcome,'done');
  await assert.rejects(()=>tools.complete_task.execute({outcome:'maybe',summary:'x',checks:'x'}));
});

test('peer routing rejects unknown and self recipients',async t=>{
  const {root,tools}=fixture(t);
  await assert.rejects(()=>tools.send_peer.execute({target:'outside',message:'x'}));
  await assert.rejects(()=>tools.send_peer.execute({target:'a',message:'x'}));
  await tools.send_peer.execute({target:'b',message:'finding'});
  assert.equal(fs.readdirSync(path.join(root,'outbox')).length,1);
});

test('handoff preserves rationale and open issues',async t=>{
  const {root,tools}=fixture(t);
  await tools.complete_task.execute({outcome:'done',summary:'implemented',checks:'unit passed',rationale:'stable API',open_issues:'integration remains'});
  const result=JSON.parse(fs.readFileSync(path.join(root,'completion.json')));
  assert.equal(result.rationale,'stable API');assert.equal(result.open_issues,'integration remains');
});

test('peer events carry type and correlation and reject unsupported types',async t=>{
  const {root,tools}=fixture(t);
  await tools.send_peer.execute({target:'b',message:'Which field?',event_type:'question',correlation_id:'interface-1'});
  const item=JSON.parse(fs.readFileSync(path.join(root,'outbox',fs.readdirSync(path.join(root,'outbox'))[0])));
  assert.equal(item.event_type,'question');assert.equal(item.correlation_id,'interface-1');
  await assert.rejects(()=>tools.send_peer.execute({target:'b',message:'change plan',event_type:'command'}));
});
