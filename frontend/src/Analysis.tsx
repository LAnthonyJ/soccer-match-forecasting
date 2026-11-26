import { useEffect, useRef, useState } from 'react'
import { fmtTime, isStatic, labels, type Fixture } from './data'
import { forecastLine, localZoneLabel } from './fanFixture'
import { queryCatalog } from '../../cloudflare/lib/catalog.mjs'
import './Analysis.css'

type Fact = {id:string;tool:string;text:string;stage:string}
type Packet = {fixture_id:string;as_of:string;fact_hash:string;facts:Fact[]}
type Section = {title:string;text:string;fact_ids:string[]}
type Report = {fixture_id:string;as_of:string;fact_hash:string;source:'template'|'deepseek';model?:string;
  fallback_reason?:string;generated_at?:string;answer:string;sections:Section[]}
type Turn = {question:string;answer:string;source:string;reason?:string;choices?:{id:string;label:string}[]}

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
  const response=await fetch(path,{...options,signal})
  if (!response.ok || !response.headers.get('content-type')?.includes('json')) throw new Error(String(response.status))
  return response.json()
}
function fromFacts(packet:Packet):Report {
  const groups:[string,string[]][]=[['赛前判断',['probabilities','favourite']],['球队近况',['home_form','away_form','expected_goals']],
    ['市场对照',['market','market_gap','blended_gap']],['资料边界',['injuries']]]
  const sections=groups.flatMap(([title,keys])=>{
    const selected=keys.flatMap(k=>packet.facts.filter(f=>f.stage==='prematch'&&f.id===k))
    return selected.length?[{title,text:selected.map(f=>f.text).join(''),fact_ids:selected.map(f=>f.id)}]:[]
  })
  return {...packet,source:'template',sections,answer:sections.map(s=>s.text).join('\n')}
}
async function staticPacket(f:Fixture,signal:AbortSignal):Promise<Packet> {
  let data:Packet
  try {data=await json('./analysis/facts/'+encodeURIComponent(f.id)+'.json',signal)}
  catch(err){
    if(signal.aborted) throw err
    const season=await json('./analysis/archive/'+encodeURIComponent(f.season)+'.json',signal)
    data=season[f.id]
  }
  if(!data || data.fixture_id!==f.id || !Array.isArray(data.facts)) throw new Error('facts')
  return data
}
async function readReport(f:Fixture,signal:AbortSignal):Promise<Report> {
  if(gateway()){
    try {
      const report=await json('/api/matches/'+encodeURIComponent(f.id)+'/analysis',signal)
      if(report.fixture_id===f.id && Array.isArray(report.sections) && report.fact_hash) return report
    }catch(err){if(signal.aborted||!isStatic())throw err}
  }
  const packet=await staticPacket(f,signal)
  try {
    const index=await json('./analysis/reports/index.json',signal)
    if(index[f.id]===packet.fact_hash){
      const saved=await json('./analysis/reports/'+encodeURIComponent(f.id)+'.json',signal)
      if(saved.source==='deepseek' && saved.fixture_id===f.id && saved.fact_hash===packet.fact_hash && Array.isArray(saved.sections))return saved
    }
  }catch(err){if(signal.aborted)throw err}
  return fromFacts(packet)
}


async function staticReply(packet:Packet,question:string,signal:AbortSignal):Promise<Turn> {
  const lookup=await queryCatalog(question,new Date(),Intl.DateTimeFormat().resolvedOptions().timeZone,
    ()=>json('./analysis/catalog.json',signal))
  if(lookup)return {question,source:'template',answer:lookup.sections.map(s=>s.text).join(' ')||'赛程资料暂不可用。',choices:lookup.choices}
  const topic = /伤|停赛|首发|阵容|injur|lineup/i.test(question) ? ['injuries']
    : /市场|赔率|盘口|分歧/.test(question) ? ['market','market_gap']
    : /截止|窗口|冻结时间/.test(question) ? ['protocol_cutoff']
    : /几点|开球|何时|比赛时间/.test(question) ? ['kickoff']
    : /预计进球|比分分布/.test(question) ? ['expected_goals','goals_comparison']
    : /近.*(进球|进攻|攻防)|进球更多|进攻表现/.test(question) ? ['home_form','away_form']
    : /近期|近况|状态/.test(question) ? ['home_form','away_form','form_comparison']
    : /平局/.test(question) ? ['draw_margin','expected_goals']
    : /接近|看好|概率|胜|预测|两队|赢|原因|为什么/.test(question) ? ['probabilities','favourite','goals_comparison']
    : []
  const answer=topic.flatMap(id=>packet.facts.filter(f=>f.id===id && f.stage==='prematch').map(f=>f.text)).join(' ')
  return {question,source:'template',answer:answer || '当前资料无法回答这个问题。可以问本场胜平负概率、球队近况或市场比较。'}
}

function localAnswerTime(text:string) {
  const iso=/\b\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)\b/g
  if(!iso.test(text))return text
  const local=text.replace(iso,time=>fmtTime(time))
  return local.includes('UTC')?local.replaceAll('UTC',localZoneLabel()):local+'（'+localZoneLabel()+'）'
}
export function AnalysisPanel({f,startOpen=false}:{f:Fixture;startOpen?:boolean}) {
  return <FixtureAnalysis key={f.id} f={f} startOpen={startOpen}/>
}

function FixtureAnalysis({f,startOpen}:{f:Fixture;startOpen:boolean}) {
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
  },[requested,retry,f.id,f.trace?.revision,f.prediction?.source])
  useEffect(()=>{
    if(tab==='ask' && report && focusWanted.current){
      input.current?.focus({preventScroll:true});focusWanted.current=false
    }
  },[tab,report])
  function show(view:'report'|'ask'){
    setRequested(true);setTab(tab===view?null:view)
    focusWanted.current=tab!==view && view==='ask'
  }
  async function ask(text:string){
    const q=text.trim()
    if(!q||!report||sending||refresh)return
    askControl.current?.abort()
    const c=new AbortController();askControl.current=c
    setQuestion(q);setSending(true);setAskError('')
    try{
      let turn:Turn
      if(gateway()){
        const history=turns.slice(-3).flatMap(t=>[{role:'user',content:t.question},{role:'assistant',content:t.answer.slice(0,500)}])
        const reply=await json('/api/analysis/ask',c.signal,{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({fixture_id:f.id,fact_hash:report.fact_hash,question:q,history,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone})})
        if(reply.fact_hash!==report.fact_hash || reply.fixture_id!==f.id)throw new Error('409')
        if(typeof reply.answer!=='string'||!reply.answer)throw new Error('empty')
        turn={question:q,answer:reply.answer,source:reply.source,reason:reply.fallback_reason,choices:reply.choices}
      }else{
        const packet=await staticPacket(f,c.signal)
        if(packet.fact_hash!==report.fact_hash)throw new Error('409')
        turn=await staticReply(packet,q,c.signal)
      }
      if(!c.signal.aborted){setTurns(items=>[...items,turn].slice(-6));setQuestion('')}
    }catch(err){
      if(!c.signal.aborted){
        const status=err instanceof Error?err.message:''
        if(status==='409'){setRefresh(true);setAskError('本场资料已更新，请读取新版再发送。')}
        else setAskError(status==='429'?'当前追问额度已用完，原问题已保留。':'追问暂时未送达，原问题已保留。')
      }
    }finally{if(!c.signal.aborted)setSending(false)}
  }
  const form=f.prediction?.form
  const suggestions=[forecastLine(f,true)==='两队接近'?'两队为什么这么接近？':'这场更看好谁？',
    form?.home&&form?.away?'两队近期的进攻表现如何？':'本场预计进球是多少？',f.prediction?.market?'市场也这么看吗？':'这场有伤停消息吗？']
  return <section className="ai-panel" aria-label="赛事分析">
    <div className="ai-actions">
      <button type="button" className="ai-entry" aria-expanded={tab==='report'} onClick={()=>show('report')}>{tab==='report'?'收起分析':'展开分析'}<span aria-hidden="true">{tab==='report'?'−':'＋'}</span></button>
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
        <div className="ai-prompts">{suggestions.map(q=><button disabled={sending||refresh} key={q} onClick={()=>void ask(q)}>{q}</button>)}</div>
        <div aria-live="polite">{turns.map((t,i)=><div key={i} className="ai-turn"><b>{t.question}</b>
          <p>{localAnswerTime(t.answer)}</p><small>{t.source==='deepseek'?'AI 生成':'基础数据解读'} {t.reason&&notices[t.reason]}</small>
          {t.choices?.map(c=><p key={c.id}><a className="ai-link" href={'#/match/'+encodeURIComponent(c.id)}>{localAnswerTime(c.label)}</a></p>)}</div>)}</div>
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
