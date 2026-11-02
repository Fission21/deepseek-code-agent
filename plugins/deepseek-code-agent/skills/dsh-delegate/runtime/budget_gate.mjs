import {execFile} from 'node:child_process';
import {randomUUID,createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs';
import path from 'node:path';

// The request and opaque-retry bounds were audited against these DSH modules.
// Upgrades require a new transport audit, not an implicit broader spend permit.
const AUDITED={
  'dsh-llm':'9132c8a8053ee82b9fb1ded4f98c85cf557f288a15a85c552c6b1fb319ead120',
  'dsh-llm-deepseek':'226e2047b843f954d8478207613f3e44448671c6f98f8edee77008d0fe005484',
};
export function assertAuditedRuntime(base) {
  for(const [module,expected] of Object.entries(AUDITED)) {
    let hash;try {hash=createHash('sha256').update(fs.readFileSync(path.join(base,module,'lib/index.js'))).digest('hex');}catch {}
    if(hash!==expected)throw Error('DSH runtime has not been audited for budget bounds: '+module);
  }
}
export function installToolBudgetGuard(ctx) {
  ctx.tools.guard(exec=>['web_search','web_fetch'].includes(exec.name)
    ? 'Native web helper is outside the budget gate. Use already supplied sources or the assigned Chrome task; ask the parent if research is required.' : undefined);
}
function hasMedia(value) {
  if(!value||typeof value!=='object')return false;
  if(['image','file'].includes(value.type))return true;
  return Object.values(value).some(hasMedia);
}

function ledger(config,operation,data) {
  return new Promise((resolve,reject)=>{
    const proc=execFile(config.budgetPython || 'python3',[
      fileURLToPath(new URL('../scripts/budget.py',import.meta.url)),
      '--root',config.budget.root,operation,
    ],{timeout:15000,maxBuffer:65536},(error,stdout)=>{
      try {const result=JSON.parse(stdout);if(error || result.error)reject(Error(result.error || 'Budget ledger failed'));else resolve(result);}
      catch {reject(Error('Budget ledger unavailable; request denied'));}
    });
    proc.stdin.end(JSON.stringify(data));
  });
}

/** Wrap every native LLM stream, including model-loop retries. No polling model. */
export function installBudgetGate(ctx,config,rpc=(op,data)=>ledger(config,op,data)) {
  ctx.on('llm/stream',(options,next)=>(async function*(){
    if(!config.budget?.root || !config.budget?.pool_id)throw Error('No authorized DSH budget binding');
    const info=await ctx.llm.resolveModelInfo(options.provider,options.model,options.signal);
    const input=info.context?.contextWindow, output=options.maxTokens ?? info.defaultMaxTokens;
    if(!Number.isSafeInteger(input)||input<=0||!Number.isSafeInteger(output)||output<=0)throw Error('Unknown model token bounds; budget denied');
    const request_id=randomUUID();
    await rpc('reserve',{pool_id:config.budget.pool_id,request_id,owner:config.invocation,
      provider:options.provider,model:options.model,input_tokens:input,output_tokens:output,
      opaque_retry_possible:hasMedia(options.messages)});
    let usage,successful=false,complete=false;
    try {
      for await(const chunk of next()) {
        if(chunk.type==='usage')usage=chunk.usage;
        if(chunk.type==='finish')successful=['stop','max-tokens','tool-calls'].includes(chunk.reason?.kind);
        yield chunk;
      }
      complete=true;
    } finally {
      // Cancellation, consumer abandonment, failures and missing usage keep funds.
      if(complete&&successful&&usage)await rpc('settle',{request_id,usage});
    }
  })());
}
