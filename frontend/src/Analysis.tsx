import { useEffect, useRef, useState } from 'react'
import { fmtTime, isStatic, labels, type Fixture } from './data'
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

export function AnalysisPanel({f,startOpen=false}:{f:Fixture;startOpen?:boolean}) {
  const [open,setOpen]=useState(startOpen), [report,setReport]=useState<Report|null>(null)
  const [retry,setRetry]=useState(0),[error,setError]=useState(''),[turns,setTurns]=useState<Turn[]>([])
  const [question,setQuestion]=useState(''),[pending,setPending]=useState(false),[refresh,setRefresh]=useState(false)
  const askControl=useRef<AbortController|null>(null)
  useEffect(()=>{
    if(!open)return
    const c=new AbortController()
    setReport(null);setError('');setRefresh(false);setTurns([]);setQuestion('');setPending(false)
    readReport(f,c.signal).then(r=>{if(!c.signal.aborted)setReport(r)})
      .catch(()=>{if(!c.signal.aborted)setError('分析报告暂时无法读取。')})
    return ()=>{c.abort();askControl.current?.abort()}
  },[open,f.id,retry])
  async function ask(){
    const q=question.trim()
    if(!q||!report||pending||refresh)return
    askControl.current?.abort()
    const c=new AbortController();askControl.current=c
    setPending(true);setError('')
    try {
      const history=turns.slice(-3).flatMap(t=>[{role:'user',content:t.question},{role:'assistant',content:t.answer.slice(0,500)}])
      const reply=await json('/api/analysis/ask',c.signal,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({fixture_id:f.id,fact_hash:report.fact_hash,question:q,history,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone})})
      if(c.signal.aborted)return
      if(reply.fact_hash!==report.fact_hash || reply.fixture_id!==f.id)throw new Error('409')
      if(typeof reply.answer!=='string'||!reply.answer)throw new Error('answer')
      setTurns(items=>[...items,{question:q,answer:reply.answer,source:reply.source,reason:reply.fallback_reason,choices:reply.choices}].slice(-6));setQuestion('')
    }catch(err){
      if(!c.signal.aborted){
        const status=err instanceof Error?err.message:''
        if(status==='409'){setRefresh(true);setError('比赛资料已更新，请重新读取报告后追问。')}
        else setError(status==='429'?'本次追问额度已用完，已有报告仍可阅读。':'追问暂时未送达。可以保留问题并重试。')
      }
    }finally{if(!c.signal.aborted)setPending(false)}
  }
  return <section className="panel ai-panel" aria-label="赛事分析">
    <button type="button" className="ai-entry" aria-expanded={open} onClick={()=>setOpen(!open)}>
      <span>{open?'收起赛事分析':'阅读 AI 赛事分析'}</span><span aria-hidden="true">{open?'−':'＋'}</span></button>
    {open&&<div className="ai-body">
      {!report&&!error&&<p role="status">正在读取本场报告…</p>}
      {report&&<><p className="ai-credit">{report.source==='deepseek'?'AI 生成 · '+(report.model||'DeepSeek'):'基础数据解读 · 模板生成'}
        {' · '}{f.prediction?labels[f.prediction.source]:'赛程待核实'}</p>
        <p className="muted">资料截止 {fmtTime(report.as_of)}{f.prediction&&' · '+f.prediction.model_version}</p>
        {report.source==='deepseek'&&report.generated_at&&<small>报告生成于 {fmtTime(report.generated_at)}</small>}
        {report.fallback_reason&&notices[report.fallback_reason]&&<p className="muted">{notices[report.fallback_reason]}</p>}
        {report.sections.map(s=><div className="ai-section" key={s.title}><h4>{s.title}</h4><p>{s.text}</p></div>)}
        {f.evidence&&<a className="ledger" href={f.evidence} target="_blank" rel="noopener">核对本场赛前见证 ↗</a>}
        <a className="ledger" href={'./analysis-evidence.html?fixture='+encodeURIComponent(f.id)}>核对本场分析依据</a>
        {gateway()?<><div aria-live="polite">{turns.map((t,i)=><div key={i} className="ai-turn"><b>{t.question}</b>
          <p>{t.answer}</p><small>{t.source==='deepseek'?'AI 生成':'基础数据解读'} {t.reason&&notices[t.reason]}</small>
          {t.choices?.map(c=><p key={c.id}><a href={'#/match/'+encodeURIComponent(c.id)}>{c.label}</a></p>)}</div>)}</div>
          <form onSubmit={e=>{e.preventDefault();void ask()}}><label>关于这场比赛的追问
            <input maxLength={350} value={question} onChange={e=>setQuestion(e.target.value)} placeholder="为什么平局概率低于主胜？"/></label>
            <button disabled={pending||refresh||!question.trim()}>{pending?'正在分析…':'发送追问'}</button></form>
          <small>实时 AI 每位访客每天最多 5 次。只需填写赛事问题。</small></>:
          <p className="muted">这里可阅读共享报告。国内实时追问入口待验收。</p>}
      </>}
      {error&&<p role="alert">{error} {(!report||refresh)&&<button onClick={()=>setRetry(n=>n+1)}>重新读取报告</button>}</p>}
    </div>}
  </section>
}
