import { useEffect, useRef, useState } from 'react'
import { type Dashboard, type Fixture, type Answer, isStatic, localAnswer, nameOf } from './data'
const examples=['这场比赛的胜平负概率是多少？','模型和市场的分歧在哪里？','过去一季模型比市场好吗？','这场比赛有哪些伤停？']
export function Ask({data,f,initial,select}:{data:Dashboard;f:Fixture|undefined;initial:string;select:(id:string)=>void}) {
  const [question,setQuestion]=useState(''),[asked,setAsked]=useState(''),[answer,setAnswer]=useState<Answer|null>(null),[pending,setPending]=useState(false)
  const control=useRef<AbortController|null>(null)
  async function ask(q:string) {
    q=q.trim()
    if(!q) return
    control.current?.abort()
    const c=new AbortController();control.current=c
    setAsked(q);setQuestion('');setAnswer(null);setPending(true)
    try {
      if(isStatic()) setAnswer(localAnswer(q,f,data))
      else {
        const res=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q,fixture_id:f?.id,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone}),signal:c.signal})
        if(!res.ok) throw new Error()
        const body:Answer=await res.json()
        if(!c.signal.aborted) setAnswer(body)
      }
    } catch {if(!c.signal.aborted) setAnswer({answer:'问答服务暂不可用，请重试。'})}
    finally {if(!c.signal.aborted) setPending(false)}
  }
  useEffect(()=>{if(initial) void ask(initial);return ()=>control.current?.abort()},[]) // route change remounts with the selected fixture
  return <section className="panel ask-panel"><div className="ask-icon" aria-hidden="true">?</div><h2>询问预测助手</h2>
    <p className="muted">{f?'当前比赛 · '+nameOf(f,'home')+' vs '+nameOf(f,'away'):'可查询已收录的英超比赛与模型成绩。'}</p>
    <div className="suggestions">{examples.map(q=><button key={q} onClick={()=>void ask(q)}>{q}</button>)}</div>
    {asked&&<div className="conversation"><div className="asked">{asked}</div><div aria-live="polite" className="reply">{pending?'正在查找快照…':answer?.answer}</div>
      {answer?.choices?.map(c=><button className="choice" key={c.id} onClick={()=>select(c.id)}>{c.label} →</button>)}</div>}
    <form className="ask-form" onSubmit={e=>{e.preventDefault();void ask(question)}}><label htmlFor="question">你的问题</label><div><input id="question" value={question} maxLength={350} autoComplete="off" onChange={e=>setQuestion(e.target.value)} placeholder="例如：周末阿森纳踢谁？"/><button disabled={!question.trim()}>提问 ↑</button></div></form></section>
}
