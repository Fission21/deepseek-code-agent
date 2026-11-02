import test from 'node:test';
import assert from 'node:assert/strict';
import {installBudgetGate, installToolBudgetGuard, assertAuditedRuntime} from '../skills/dsh-delegate/runtime/budget_gate.mjs';

function setup(rpc) {
  let hook;
  const ctx={on(name,fn){assert.equal(name,'llm/stream');hook=fn;},llm:{async resolveModelInfo(){return {context:{contextWindow:100},defaultMaxTokens:50};}}};
  installBudgetGate(ctx,{budget:{root:'/fixture',pool_id:'pool'},invocation:'turn'},rpc);
  return hook;
}
const options={provider:'deepseek-official',model:'deepseek-flash',maxTokens:50,messages:[]};
async function consume(stream){for await(const item of stream){} }

test('rejected reservation prevents provider dispatch',async()=>{
  let dispatched=false;
  const hook=setup(async()=>{throw Error('Budget exhausted');});
  await assert.rejects(consume(hook(options,()=>{dispatched=true;return [];})),/Budget exhausted/);
  assert.equal(dispatched,false);
});
test('only successful terminal stream settles; aborted and partial streams keep reservation',async()=>{
  for(const reason of ['stop','error','aborted']) {
    const ops=[];const hook=setup(async(op,data)=>{ops.push([op,data]);return {};});
    await consume(hook(options,async function*(){yield {type:'usage',usage:{inputTokens:5,outputTokens:2}};yield {type:'finish',reason:{kind:reason}};}));
    assert.deepEqual(ops.map(x=>x[0]),reason==='stop'?['reserve','settle']:['reserve']);
    assert.equal(ops[0][1].input_tokens,100);
  }
});
test('retry obtains a new reservation and missing budget fails before dispatch',async()=>{
  const ids=[];const hook=setup(async(op,data)=>{if(op==='reserve')ids.push(data.request_id);});
  await consume(hook(options,async function*(){}));await consume(hook(options,async function*(){}));
  assert.equal(new Set(ids).size,2);
  let fn;installBudgetGate({on(n,h){fn=h;}},{},async()=>{});
  await assert.rejects(consume(fn(options,()=>{throw Error('must not dispatch');})),/budget/i);
});
test('paid web helpers cannot bypass the model budget and unknown runtime is refused',()=>{
  let guard;installToolBudgetGuard({tools:{guard(fn){guard=fn;}}});
  assert.match(guard({name:'web_search'}),/budget/i);
  assert.match(guard({name:'web_fetch'}),/budget/i);
  assert.equal(guard({name:'mcp_huashu_snapshot'}),undefined);
  assert.throws(()=>assertAuditedRuntime('/missing-runtime'),/runtime/i);
});
