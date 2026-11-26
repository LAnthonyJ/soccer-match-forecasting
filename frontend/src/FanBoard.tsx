import { useState, useEffect } from 'react'
import { type FanBoard, type Fixture, fmtTime, nameOf, pct } from './data'
import { fanPhase, forecastLine, localZoneLabel } from './fanFixture'
import { club } from './TeamMark'

export interface Filters {season:string;week:string;date:string;q:string}
const dayFormat=new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'2-digit',day:'2-digit'})
const clockFormat=new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',hourCycle:'h23'})
const weekdays='日一二三四五六'
function dayOf(f:Fixture) {
  if (!f.kickoff_utc) return f.date
  const local=new Date(f.kickoff_utc)
  return Number.isFinite(+local)?dayFormat.format(local):f.date
}
function dateLabel(key:string,short=false) {
  const d=new Date(key+'T12:00:00Z')
  if(!Number.isFinite(+d)) return '日期待定'
  const day='周'+weekdays[d.getUTCDay()]
  return short?day+' '+(d.getUTCMonth()+1)+'/'+d.getUTCDate():day+' · '+(d.getUTCMonth()+1)+' 月 '+d.getUTCDate()+' 日'
}
function clock(f:Fixture) {return f.kickoff_utc?clockFormat.format(new Date(f.kickoff_utc)):'待定'}
function order(a:Fixture,b:Fixture) {
  return dayOf(a).localeCompare(dayOf(b))||(a.kickoff_utc||'~').localeCompare(b.kickoff_utc||'~')||a.id.localeCompare(b.id)
}

export function WeekTools({data,filters,change,reset}:{data:FanBoard;filters:Filters;change:(f:Partial<Filters>,replace?:boolean)=>void;reset:()=>void}) {
  const weeks=data.week_catalog[filters.season]||[]
  const index=weeks.indexOf(filters.week)
  return <div className="fan-tools" data-testid="round-nav">
    <div className="fan-round-picker" role="group" aria-label="轮次导航">
      <button type="button" aria-label="上一轮" disabled={index<=0} onClick={()=>change({week:weeks[index-1],date:''})}>‹</button>
      <label className="fan-week-select"><select aria-label="比赛周" value={filters.week} onChange={e=>change({week:e.target.value,date:''})}>{weeks.map(w=><option key={w}>{w}</option>)}</select><span aria-hidden="true">⌄</span></label>
      <button type="button" aria-label="下一轮" disabled={index<0||index+1>=weeks.length} onClick={()=>change({week:weeks[index+1],date:''})}>›</button>
    </div>
    <button className="fan-reset" onClick={reset} aria-label="返回本轮">本轮</button>
    <label className="fan-search"><span aria-hidden="true" className="search-glass"/><input type="search" aria-label="搜索球队" placeholder="搜索本轮球队" maxLength={80} value={filters.q} onChange={e=>change({q:e.target.value},true)}/></label>
  </div>
}

function Side({f,side}:{f:Fixture;side:'home'|'away'}) {
  return <span className="fan-team"><i aria-hidden="true" style={{backgroundColor:club(f[side])[0]}}/>{nameOf(f,side)}</span>
}
function Card({f,now,active,highlight,open}:{f:Fixture;now:number;active:boolean;highlight:boolean;open:(id:string)=>void}) {
  const phase=fanPhase(f,now)
  const forecast=phase==='scheduled'&&!!f.prediction
  const settled=phase==='finished'
  const status=settled?'已完场':phase==='conflict'?'比分待核实':phase==='waiting'?'等待赛果':phase==='unscheduled'?'开球时间待定':forecast?'看预测':'赛程已确认'
  return <button type="button" data-testid="fixture-row" data-fixture-id={f.id} aria-current={active?'true':undefined}
      className={'fan-row'+(highlight?' fan-next':'')+(settled?' fan-done':'')+(active?' fan-selected':'')} onClick={()=>open(f.id)}>
    <span className="fan-time">{settled?'完场':phase==='unscheduled'?'待定':clock(f)}</span>
    <span className="fan-pair"><Side f={f} side="home"/><Side f={f} side="away"/></span>
    {settled?<span className="fan-goals" aria-label={'比分 '+f.hg+' 比 '+f.ag}><b>{f.hg}</b><b>{f.ag}</b></span>:
      <span className="fan-row-tail"><span className={forecast?'fan-row-link':''}>{status}{forecast&&<span aria-hidden="true"> ›</span>}</span>
        {forecast&&<span className="fan-brief" data-testid="forecast-summary">{forecastLine(f,true)}</span>}</span>}
  </button>
}
export function featuredGame(fixtures:Fixture[],now:number) {
  return fixtures.filter(f=>fanPhase(f,now)==='scheduled'&&f.prediction).sort(order)[0]
}
export function Featured({f,open}:{f:Fixture;open:(id:string)=>void}) {
  if(!f.prediction)return null
  const names=[nameOf(f,'home')+'胜','平局',nameOf(f,'away')+'胜']
  return <aside className="fan-spotlight" aria-label="最近一场预测">
    <div className="fan-spot-top"><span>下一场预测</span><span>{fmtTime(f.kickoff_utc)} · {localZoneLabel()}</span></div>
    <div className="fan-spot-fixture">
      <h2>{nameOf(f,'home')}<em>对</em>{nameOf(f,'away')}</h2>
      <p>{forecastLine(f,true)}</p>
    </div>
    <div className="fan-spot-probs">{f.prediction.a.map((p,i)=><div key={i}><small>{names[i]}</small><b>{pct(p)}</b></div>)}</div>
    <button className="fan-spot-link" onClick={()=>open(f.id)}>查看本场预测 <span aria-hidden="true">↗</span></button>
  </aside>
}

interface ListProps {
  data:FanBoard;games:Fixture[];filters:Filters;selected:string;loading:boolean;error:string;
  change:(f:Partial<Filters>,replace?:boolean)=>void;open:(id:string)=>void;retry:()=>void;now:number
}
export function FanList({data,games,filters,selected,loading,error,change,open,retry,now}:ListProps) {
  const [more,setMore]=useState(false)
  useEffect(()=>{setMore(false)},[filters.season,filters.week])
  const days=[...new Set(games.map(dayOf))].sort()
  const q=filters.q.trim().toLowerCase()
  const visible=games.filter(f=>(!filters.date||dayOf(f)===filters.date)&&(!q||
    [f.home,f.away,nameOf(f,'home'),nameOf(f,'away'),...(data.team_aliases?.[f.home]||[]),...(data.team_aliases?.[f.away]||[])]
      .some(s=>s.toLowerCase().includes(q)))).sort(order)
  const remaining=visible.filter(f=>fanPhase(f,now)!=='finished')
  const complete=visible.filter(f=>fanPhase(f,now)==='finished')
  const next=featuredGame(visible,now)?.id
  const group=(groupGames:Fixture[],done:boolean)=>{
    const groups=new Map<string,Fixture[]>()
    for(const f of groupGames){const key=dayOf(f);const fixtures=groups.get(key)||[];fixtures.push(f);groups.set(key,fixtures)}
    return [...groups.entries()].map(([key,items])=><section className="fan-date-group" data-testid="fixture-date" key={key} aria-label={dateLabel(key)}>
      <h3><button onClick={()=>change({date:filters.date===key?'':key})} aria-pressed={filters.date===key}>{dateLabel(key)}</button>
        <span>{done?'已完场':'赛程'} · {items.length} 场</span></h3>
      {items.map(f=><Card key={f.id} f={f} open={open} now={now} active={selected===f.id} highlight={f.id===next}/>)}
    </section>)
  }
  return <section className="fan-list" aria-label="本轮比赛">
    <div className="fan-list-top"><h2>{filters.q?'搜索结果':'比赛日程'}</h2>
      <button className="fan-date-toggle" aria-expanded={more} onClick={()=>setMore(!more)}>选择日期 <span aria-hidden="true">⌄</span></button></div>
    {more&&<div className="fan-days" role="group" aria-label="日期筛选">
      <button aria-pressed={!filters.date} onClick={()=>change({date:''})}>全部日期</button>
      {days.map(d=><button key={d} aria-pressed={filters.date===d} onClick={()=>change({date:d})}>{dateLabel(d,true)}</button>)}</div>}
    {filters.date&&!more&&<button className="fan-filter-tag" onClick={()=>change({date:''})}>{dateLabel(filters.date)}　× 清除</button>}
    {loading?<p role="status" className="fan-empty">正在读取本轮赛程…</p>:error?
      <div role="alert" className="fan-empty">{error} <button onClick={retry}>重试</button></div>:<>
        {data.season_finished&&filters.season===data.current_season&&filters.week===data.current_week&&<p className="fan-empty">本季已结束，下季赛程待公布。</p>}
        {group(remaining,false)}
        {complete.length>0&&<div className="fan-results"><h2>本轮已结束</h2>{group(complete,true)}</div>}
        {!visible.length&&<div className="fan-empty">{q?'本轮没有找到匹配的球队。':filters.date?'当天没有排定的比赛。':'这一轮暂未公布赛程。'}
          {(q||filters.date)&&<button onClick={()=>change({q:'',date:''})}>查看本轮全部比赛</button>}</div>}
    </>}
  </section>
}
