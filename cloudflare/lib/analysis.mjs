import {ApiError, completeReport, isCurrent, jsonBody, loadFacts, renderCalls, reviewFacts, templateCalls, validCached, viewFor, visitorKey} from './facts.mjs';
import {cached,reserve,settle} from './budget.mjs';
import {configured,decode,infer,MODEL,prepare,PROMPT,usage} from './provider.mjs';
import {answerFanQuestion,validContext} from './fanqa.mjs';

const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{
  'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'
}});
function response(packet,view,source,sections,reason,now) {
  const fallback=reason==='unsupported_data'?'目前缺少可核实的实时伤停、首发、天气或战术资料。':
    view==='postmatch'?'当前数据尚无已核实的赛后复盘资料。':'当前可核实的资料不足以回答这个问题。';
  return {schema:'epl-analysis-response/v1',fixture_id:packet.fixture_id,revision:packet.revision,
    fact_hash:packet.fact_hash,as_of:packet.as_of,forecast_source:packet.source,available_topics:packet.available_topics,view,
    source,model:source==='deepseek'?MODEL:null,prompt_version:PROMPT,generated_at:now.toISOString(),
    fallback_reason:reason,answer:sections.map(s=>s.text).join('\n\n')||fallback,sections};
}
function validateAsk(body) {
  if (!body || typeof body!=='object' || Array.isArray(body) ||
      typeof body.fixture_id!=='string' || typeof body.question!=='string' ||
      !body.question.trim() || body.question.length>350 ||
      !/^[0-9a-f]{64}$/.test(body.fact_hash) ||
      Object.keys(body).some(k=>!['fixture_id','question','history','fact_hash','timezone','context'].includes(k))) throw new ApiError(400,'invalid_question');
  if(!validContext(body.context))throw new ApiError(400,'invalid_context');
  const history=body.history??[];
  if (!Array.isArray(history) || history.length>6 || history.some(t=>!t || !['user','assistant'].includes(t.role) ||
      typeof t.content!=='string' || t.content.length>500)) throw new ApiError(400,'invalid_history');
  const timezone=body.timezone??'Europe/London';
  try { if (typeof timezone!=='string' || timezone.length>80) throw new Error(); new Intl.DateTimeFormat('en',{timeZone:timezone}); }
  catch { throw new ApiError(400,'invalid_timezone'); }
  return {context:body.context,question:body.question.trim(),id:body.fixture_id,factHash:body.fact_hash,timezone,
    history:history.filter(t=>t.role==='user').slice(-3).map(t=>t.content.slice(0,300))};
}

export async function handle(context,kind,options={}) {
  const {env,request}=context, url=new URL(request.url);
  const now=options.now??new Date(), fetcher=options.fetcher??fetch;
  try {
    if (request.method!==(kind==='ask'?'POST':'GET')) return json({error:'method_not_allowed'},405);
    let question='', history=[], id, expected, timezone, chatContext;
    const mirror=kind==='report' && url.searchParams.get('cached')==='1';
    if (kind==='ask') {
      if (request.headers.get('sec-fetch-site')==='cross-site' ||
          (request.headers.has('origin') && request.headers.get('origin')!==url.origin)) return json({error:'cross_origin'},403);
      if (!request.headers.get('content-type')?.split(';')[0].trim().toLowerCase().match(/^application\/json$/)) return json({error:'json_required'},415);
      ({id,question,history,factHash:expected,timezone,context:chatContext}=validateAsk(await jsonBody(request,8192)));
    } else id=context.params.id;
    const packet=await loadFacts(env,request,id);
    if (expected && packet.fact_hash!==expected) return json({error:'facts_changed',fact_hash:packet.fact_hash},409);
    const view=viewFor(question);
    const base=()=>response(packet,view,'template',[],null,now);
    let scoped;
    if (kind==='ask') {
      const read=async(path,limit)=>{
        const res=await env.ASSETS.fetch(new Request(new URL(path,request.url)));
        if(!res.ok || !res.headers.get('content-type')?.includes('json'))return null;
        return jsonBody(res,limit);
      };
      try{
        scoped=await answerFanQuestion({packet,question,context:chatContext,timezone,now,
          loadPacket:fid=>loadFacts(env,request,fid),
          loadCatalog:()=>read('/analysis/catalog.json',2_500_000),
          loadHistory:async(fid,scope)=>{
            const [season,home]=fid.split('_');
            return (await read('/analysis/team-history/'+season+'/'+scope+'/'+home+'.json',12_000_000))?.[fid];
          },
          loadReview:async fid=>reviewFacts(env,request,fid===packet.fixture_id?packet:await loadFacts(env,request,fid))});
      }catch(err){if(err.message==='409')throw new ApiError(409,'context_changed');throw err;}
      const interpret=['probabilities','form','draw','goals','market','review','result','score'].includes(scoped.topic);
      if(!interpret || scoped.answer_fixture_id!==id || scoped.missing_reason){
        const reason=scoped.topic==='review' && scoped.missing_reason?'missing_review':scoped.missing_reason;
        return json({...base(),...scoped,fallback_reason:reason});
      }
    }
    // A report never requests the separate result asset, even after full time.
    const review=view==='postmatch'?await reviewFacts(env,request,packet):null;
    if (view==='postmatch' && !review) return json({...base(),fallback_reason:'missing_review'});
    const facts=(review??packet).facts;
    const fallback=reason=>{
      if(scoped)return {...base(),...scoped,fallback_reason:reason,review_as_of:review?.as_of??null};
      const calls=templateCalls(facts,question,kind);
      const sections=calls.length?renderCalls(calls,facts):[];
      return {...response(packet,view,'template',kind==='report'?completeReport(sections,facts):sections,reason,now),
        review_as_of:review?.as_of??null};
    };
    const key=[PROMPT,MODEL,id,packet.fact_hash].join(':');
    if (kind==='report' && env.DB) {
      try {
        const saved=await cached(env.DB,key);
        if (saved && validCached(saved,packet)) return json({...saved,as_of:packet.as_of,available_topics:packet.available_topics});
        if (saved) return mirror?json({error:'invalid_cached_report'},503):json(fallback('unverified_analysis'));
      } catch { if (mirror) return json({error:'storage_unavailable'},503); return json(fallback('storage_unavailable')); }
    }
    if (mirror) return json({error:'report_not_cached'},404);
    if (kind==='report' && !(await isCurrent(env,request,id))) return json(fallback('outside_current_round'));
    if (!env.DB || !configured(env,now)) return json(fallback('provider_not_configured'));
    if (kind==='report' && (packet.source==='schedule' || !facts.some(f=>f.id==='probabilities'))) return json(fallback('missing_prediction'));
    let visitor='';
    if (kind==='ask') {
      const ip=request.headers.get('cf-connecting-ip');
      if (!env.VISITOR_HMAC_SECRET || env.VISITOR_HMAC_SECRET.length<24 || !ip || ip.length>80) return json(fallback('visitor_unavailable'));
      visitor=await visitorKey(ip,now.toISOString().slice(0,10),env.VISITOR_HMAC_SECRET);
    }
    let prepared;
    try { prepared=prepare(facts,kind,question,history,view); }
    catch { return json(fallback('context_too_large')); }
    const requestId=crypto.randomUUID();
    let denied;
    try { denied=await reserve(env.DB,{kind,key,visitor,estimate:prepared.estimate,now,id:requestId}); }
    catch { return json(fallback('storage_unavailable')); }
    if (denied) return json(fallback(denied));
    let amount=null,answer,reason;
    try {
      const raw=await infer(env,prepared,fetcher);
      amount=usage(raw,prepared);
      const sections=decode(raw,facts);
      const refs=new Set(sections.flatMap(s=>s.fact_ids));
      const comparisons=facts.filter(f=>['form_comparison','goals_comparison','draw_margin','market_leader'].includes(f.id));
      if (scoped && !scoped.evidence.some(id=>refs.has(id))) throw new Error('unverified_analysis');
      if (kind==='report' && comparisons.length && !comparisons.some(f=>refs.has(f.id))) throw new Error('unverified_analysis');
      if (view==='postmatch' && !facts.some(f=>f.stage==='postmatch' && refs.has(f.id))) throw new Error('unverified_analysis');
      answer={...scoped,...response(packet,view,'deepseek',kind==='report'?completeReport(sections,facts):sections,null,now),
        ...(scoped?{evidence:[...refs],as_of:scoped.as_of}:{}),
        provider_model:/^[a-zA-Z0-9._-]{1,100}$/.test(raw.model??'')?raw.model:MODEL,
        review_as_of:review?.as_of??null,generated_as_of:packet.as_of};
    } catch (e) {
      reason=['provider_limited','provider_unavailable'].includes(e.message)?e.message:
        ['TimeoutError','AbortError','TypeError'].includes(e.name)?'provider_unavailable':'unverified_analysis';
    }
    try { await settle(env.DB,requestId,amount,answer?'ok':'failed',key,kind==='report'?answer:null); }
    catch { return json(fallback('storage_unavailable')); } // reservation remains charged on failure
    return json(answer??fallback(reason));
  } catch (e) {
    return e instanceof ApiError?json({error:e.message},e.status):json({error:'facts_unavailable'},503);
  }
}
