import { useEffect, useRef, useState } from 'react'
import { fmtTime, isStatic, labels, type Fixture } from './data'
import { localZoneLabel } from './fanFixture'
import { suggestedQuestions, answerFanQuestion, type QuestionContext, type FanAnswer } from '../../cloudflare/lib/fanqa.mjs'
import './Analysis.css'

type Fact = {id:string;tool:string;text:string;stage:string}
type Packet = {fixture_id:string;as_of:string;fact_hash:string;facts:Fact[];available_topics?:string[]}
type Section = {title:string;text:string;fact_ids:string[]}
type Report = {fixture_id:string;as_of:string;fact_hash:string;source:'template'|'deepseek';model?:string;
  fallback_reason?:string;generated_at?:string;answer:string;sections:Section[];available_topics?:string[]}
type Turn = FanAnswer & {question:string;reason?:string;scope?:string}

const notices:Record<string,string>={
  daily_limit:'今日的实时追问额度已用完。',monthly_budget:'本月 AI 额度已用完。',
  outside_current_round:'本轮以外的比赛可阅读基础数据。',report_pending:'共享报告正在准备。',
  provider_limited:'模型服务暂时限流。',provider_unavailable:'实时 AI 尚未连接或暂时不可用。',
  provider_not_configured:'实时 AI 尚未启用。',storage_unavailable:'共享报告服务暂时不可用。',
  gateway_snapshot_differs:'远端报告的数据版本与当前比赛不同。',unverified_analysis:'模型返回的内容未通过事实核对。'
}

function gateway() {
  return !isStatic() || document.querySelector('meta[name="analysis-mode"]')?.getAttribute('content')==='gateway'
}
async function json(path:string,signal:AbortSignal,options:RequestInit={}) {
  const response=await fetch(path,{cache:'no-cache',...options,signal})
  if (!response.ok || !response.headers.get('content-type')?.includes('json')) throw new Error(String(response.status))
  return response.json()
}
function fromFacts(packet:Packet):Report {
  const groups:[string,string[]][]=[['赛前判断',['probabilities','favourite','top_scores']],['球队近况',['home_form','away_form','expected_goals']],
    ['市场对照',['market','market_gap','blended_gap']],['资料边界',['injuries']]]
  const sections=groups.flatMap(([title,keys])=>{
    const selected=keys.flatMap(k=>packet.facts.filter(f=>f.stage==='prematch'&&f.id===k))
    return selected.length?[{title,text:selected.map(f=>f.text).join(''),fact_ids:selected.map(f=>f.id)}]:[]
  })
  return {...packet,source:'template',sections,answer:sections.map(s=>s.text).join('\n')}
}
async function staticPacket(id:string,signal:AbortSignal):Promise<Packet> {
  let data:Packet
  try {data=await json('./analysis/facts/'+encodeURIComponent(id)+'.json',signal)}
  catch(err){
    if(signal.aborted) throw err
    const season=await json('./analysis/archive/'+encodeURIComponent(id.split('_')[0])+'.json',signal)
    data=season[id]
  }
  if(!data || data.fixture_id!==id || !Array.isArray(data.facts)) throw new Error('facts')
  return data
}
async function readReport(f:Fixture,signal:AbortSignal):Promise<Report> {
  if(gateway()){
    try {
      const report=await json('/api/matches/'+encodeURIComponent(f.id)+'/analysis',signal)
      if(report.fixture_id===f.id && Array.isArray(report.sections) && report.fact_hash) return report
    }catch(err){if(signal.aborted||!isStatic())throw err}
  }
  const packet=await staticPacket(f.id,signal)
  try {
    const index=await json('./analysis/reports/index.json',signal)
    if(index[f.id]===packet.fact_hash){
      const saved=await json('./analysis/reports/'+encodeURIComponent(f.id)+'.json',signal)
      if(saved.source==='deepseek' && saved.fixture_id===f.id && saved.fact_hash===packet.fact_hash && Array.isArray(saved.sections))return {...saved,available_topics:packet.available_topics}
    }
  }catch(err){if(signal.aborted)throw err}
  return fromFacts(packet)
}


async function staticReply(packet:Packet,question:string,context:QuestionContext|undefined,signal:AbortSignal):Promise<Turn> {
  const item=async(path:string,id:string)=>{
    try{return (await json('./analysis/'+path+'.json',signal))[id]??null}
    catch(err){if(!signal.aborted&&err instanceof Error&&err.message==='404')return null;throw err}
  }
  const reply=await answerFanQuestion({packet,question,context,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,
    loadPacket:id=>staticPacket(id,signal),loadCatalog:()=>json('./analysis/catalog.json',signal),
    loadHistory:(id,scope)=>{const [year,home]=id.split('_');return item('team-history/'+year+'/'+scope+'/'+home,id)},
    loadReview:id=>item('reviews/'+id.split('_')[0],id)})
  return {...reply,question}
}

function evidenceUrl(t:Turn){
  const fid=encodeURIComponent(t.answer_fixture_id)
  const [season,home]=fid.split('_')
  if(t.scope)return isStatic()?'./analysis/team-history/'+season+'/'+t.scope+'/'+home+'.json':'/api/matches/'+fid+'/team-history?'+new URLSearchParams({scope:t.scope,as_of:t.as_of})
  if(t.topic==='model_results')return './#/report'
  if(['review','score','result'].includes(t.topic))return isStatic()?'./analysis/reviews/'+season+'.json':'/api/fixtures/'+fid
  if(t.topic==='schedule' && isStatic())return './analysis/catalog.json'
  return './analysis-evidence.html?fixture='+fid
}
function localAnswerTime(text:string) {
  const iso=/\b\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)\b/g
  if(!iso.test(text))return text
  const local=text.replace(iso,time=>fmtTime(time))
  return local.includes('UTC')?local.replaceAll('UTC',localZoneLabel()):local+'（'+localZoneLabel()+'）'
}
export function AnalysisPanel({f,startOpen=false,batchId}:{f:Fixture;startOpen?:boolean;batchId?:string}) {
  return <FixtureAnalysis key={f.id} f={f} startOpen={startOpen} batchId={batchId}/>
}

function FixtureAnalysis({f,startOpen,batchId}:{f:Fixture;startOpen:boolean;batchId?:string}) {
  const [tab,setTab]=useState<'report'|'ask'|null>(startOpen?'report':null)
  const [requested,setRequested]=useState(startOpen),[report,setReport]=useState<Report|null>(null)
  const [retry,setRetry]=useState(0),[loadFailed,setLoadFailed]=useState(false)
  const [turns,setTurns]=useState<Turn[]>([]),[question,setQuestion]=useState('')
  const [sending,setSending]=useState(false),[askError,setAskError]=useState(''),[refresh,setRefresh]=useState(false)
  const askControl=useRef<AbortController|null>(null), input=useRef<HTMLInputElement>(null)
  const focusWanted=useRef(false), previousHash=useRef('')
  const [updateNote,setUpdateNote]=useState('')
  useEffect(()=>{
    if(!requested)return
    const c=new AbortController()
    setLoadFailed(false);setReport(null);setAskError('');setSending(false)
    readReport(f,c.signal).then(next=>{
      if(c.signal.aborted)return
      if(previousHash.current && previousHash.current!==next.fact_hash){
        setTurns([]);setUpdateNote('已载入新版赛前资料，可以用当前问题继续提问。')
      }
      previousHash.current=next.fact_hash
      setReport(next);setRefresh(false)
    }).catch(()=>{if(!c.signal.aborted)setLoadFailed(true)})
    return ()=>{c.abort();askControl.current?.abort()}
  },[requested,retry,f.id,f.trace?.revision,f.prediction?.source,batchId])
  useEffect(()=>{
    if(tab==='ask' && report && focusWanted.current){
      input.current?.focus({preventScroll:true});focusWanted.current=false
    }
  },[tab,report])
  function show(view:'report'|'ask'){
    setRequested(true);setTab(tab===view?null:view)
    focusWanted.current=tab!==view && view==='ask'
  }
  async function ask(text:string,chosen?:QuestionContext){
    const q=text.trim()
    if(!q||!report||sending||refresh)return
    askControl.current?.abort()
    const c=new AbortController();askControl.current=c
    setQuestion(q);setSending(true);setAskError('')
    try{
      const last=turns.at(-1)
      const context=chosen || (last?.answer_fixture_id&&!last.needs_choice?{fixture_id:last.answer_fixture_id,topic:last.topic,fact_hash:last.answer_fact_hash}:undefined)
      let turn:Turn
      if(gateway()){
        const history=turns.slice(-3).flatMap(t=>[{role:'user',content:t.question},{role:'assistant',content:t.answer.slice(0,500)}])
        const reply=await json('/api/analysis/ask',c.signal,{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({fixture_id:f.id,fact_hash:report.fact_hash,question:q,history,context,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone})})
        if(reply.fact_hash!==report.fact_hash || reply.fixture_id!==f.id)throw new Error('409')
        if(typeof reply.answer!=='string'||!reply.answer)throw new Error('empty')
        turn={...reply,question:q,reason:reply.fallback_reason}
      }else{
        const packet=await staticPacket(f.id,c.signal)
        if(packet.fact_hash!==report.fact_hash)throw new Error('409')
        turn=await staticReply(packet,q,context,c.signal)
      }
      if(!c.signal.aborted){setTurns(items=>[...items,turn].slice(-6));setQuestion('')}
    }catch(err){
      if(!c.signal.aborted){
        const status=err instanceof Error?err.message:''
        if(status==='409'){setTurns([]);setRefresh(true);setAskError('本场资料已更新，请读取新版再发送。')}
        else setAskError(status==='429'?'当前追问额度已用完，原问题已保留。':'追问暂时未送达，原问题已保留。')
      }
    }finally{if(!c.signal.aborted)setSending(false)}
  }
  const suggestionFacts=report?.available_topics?.map(id=>({id,text:''})) ??
    (report?.sections||[]).flatMap(section=>section.fact_ids.map(id=>({id,text:section.text})))
  const suggestions=suggestedQuestions(suggestionFacts)
  return <section className="ai-panel" aria-label="赛事分析">
    <div className="ai-actions">
      <button type="button" className="ai-entry" aria-expanded={tab==='report'} onClick={()=>show('report')}>{tab==='report'?'收起分析':report?.source==='deepseek'?'AI 赛前分析':'赛前分析'}<span aria-hidden="true">{tab==='report'?'−':'＋'}</span></button>
      <button type="button" className="ai-entry ai-ask-toggle" aria-expanded={tab==='ask'} onClick={()=>show('ask')}>问这场<span aria-hidden="true">↗</span></button>
    </div>
    {tab&&<div className="ai-body">
      {!report&&!loadFailed&&<p role="status">正在读取本场资料…</p>}
      {loadFailed&&<p role="alert">本场分析暂时无法读取。 <button className="ai-link" onClick={()=>setRetry(n=>n+1)}>重新读取报告</button></p>}
      {report&&tab==='report'&&<>
        <p className="ai-credit">{report.source==='deepseek'?'AI 生成 · '+(report.model||'DeepSeek'):'基础数据解读 · 模板生成'}{' · '}{f.prediction?labels[f.prediction.source]:'待核实'}</p>
        <p className="ai-date">数据截止 {fmtTime(report.as_of)} {localZoneLabel()}{report.generated_at&&report.source==='deepseek'?' · 生成于 '+fmtTime(report.generated_at):''}</p>
        {report.sections.map((s,i)=><div className="ai-section" key={s.title+i}><h4>{s.title}</h4><p>{s.text}</p></div>)}
        {report.fallback_reason && notices[report.fallback_reason] && <p className="ai-date">{notices[report.fallback_reason]}</p>}
        <a className="ai-link" href={'./analysis-evidence.html?fixture='+encodeURIComponent(f.id)}>核对本场分析依据</a>
      </>}
      {report&&tab==='ask'&&<>
        <h3>关于这场比赛</h3>
        {updateNote&&<p role="status" className="ai-date">{updateNote}</p>}
        <div className="ai-prompts">{suggestions.map(q=><button disabled={sending||refresh} key={q} onClick={()=>void ask(q,{fixture_id:f.id,topic:'',fact_hash:report.fact_hash})}>{q}</button>)}</div>
        <div aria-live="polite">{turns.map((t,i)=><div key={i} className="ai-turn" data-answer-fixture-id={t.answer_fixture_id} data-as-of={t.as_of} data-topic={t.topic} data-missing-reason={t.missing_reason||undefined}><b>{t.question}</b>
          <small>{t.fixture_label} {t.answer_fixture_id!==f.id&&localAnswerTime(t.answer_kickoff||'')}</small>
          <small>资料截至 {fmtTime(t.as_of)} {localZoneLabel()}</small>
          <p>{localAnswerTime(t.answer)}</p>
          {t.standings&&<div className="ai-standings" tabIndex={0}><table aria-label="按已确认比分计算的积分榜"><thead><tr>{['名次','球队','场','胜','平','负','进','失','积分'].map(h=><th key={h}>{h}</th>)}</tr></thead>
            <tbody>{t.standings.map(row=><tr key={row.team}><td>{row.rank}</td><th scope="row">{row.team_zh}</th>
              {[row.played,row.wins,row.draws,row.losses,row.gf,row.ga,row.points].map((v,j)=><td key={j}>{v}</td>)}</tr>)}</tbody></table></div>}
          {!!t.evidence?.length&&<small>依据 <a href={evidenceUrl(t)}>核对资料</a></small>}
          {t.answer_fixture_id!==f.id&&t.answer_fixture_id&&<a className="ai-link" href={'#/match/'+encodeURIComponent(t.answer_fixture_id)}>打开这场比赛 ↗</a>}<small>{t.source==='deepseek'?'AI 生成':'基础数据解读'} {t.reason&&notices[t.reason]}</small>
          {t.choices?.map(c=><p key={c.id}><>{t.needs_choice?<button type="button" className="ai-link" disabled={sending||refresh} onClick={()=>void ask(t.topic==='schedule'?'那场几点开球？':t.question,{fixture_id:c.id,topic:t.topic})}>{localAnswerTime(c.label)}</button>:<a className="ai-link" href={'#/match/'+encodeURIComponent(c.id)}>{localAnswerTime(c.label)}</a>}</></p>)}</div>)}</div>
        <form onSubmit={e=>{e.preventDefault();void ask(question)}}><label>关于这场比赛的提问
          <input ref={input} maxLength={350} readOnly={sending} value={question} onChange={e=>{setQuestion(e.target.value);if(!refresh)setAskError('')}} placeholder="例如：平局的机会有多大？"/></label>
          <button disabled={sending||refresh||!question.trim()}>{sending?'分析中…':'发送'}</button></form>
        {askError&&<div role="alert" className="ai-feedback">{askError} {refresh
          ? <button className="ai-link" onClick={()=>setRetry(n=>n+1)}>重新读取报告</button>
          : <button className="ai-link" onClick={()=>void ask(question)}>重试原问题</button>}</div>}
      </>}
    </div>}
  </section>
}
