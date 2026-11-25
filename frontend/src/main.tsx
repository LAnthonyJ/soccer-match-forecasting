import { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Ask } from './Ask'
import { Match, TeamMark, ListChance } from './Match'
import { Report } from './Report'
import { type Dashboard, type Fixture, type Report as Scores, fetchBoard, fetchSeasonFixtures, fmtTime, isStatic, fixtureStatus, nameOf, weekOf } from './data'
import './style.css'

type View = 'matches'|'report'|'ask'
function route() {
  const [path,query='']=location.hash.replace(/^#\/?/,'').split('?')
  const [page,id='']=path.split('/')
  let decoded='';try{decoded=decodeURIComponent(id)}catch{/* invalid deep link */}
  return {view:(page==='report'||page==='ask'?page:'matches') as View,id:page==='match'?decoded:'',week:new URLSearchParams(query).get('week')}
}
const tabs: [View,string][]=[['matches','比赛看板'],['report','模型成绩'],['ask','预测助手']]
function Logo() {return <span className="brand"><i aria-hidden="true"><b/><b/><b/></i>英超观察台</span>}

function App() {
  const [data,setData]=useState<Dashboard|null>(null),[failure,setFailure]=useState(''),[attempt,setAttempt]=useState(0)
  const [nav,setNav]=useState(route),[chosen,setChosen]=useState(''),[season,setSeason]=useState(''),[week,setWeek]=useState(''),[query,setQuery]=useState('')
  const [detail,setDetail]=useState<Fixture|null>(null),[detailError,setDetailError]=useState(''),[retryDetail,setRetryDetail]=useState(0)
  const [archives,setArchives]=useState<Record<string,Fixture[]>>({}),[busy,setBusy]=useState(false),[seasonError,setSeasonError]=useState(''),[retrySeason,setRetrySeason]=useState(0)
  const [browseAll,setBrowseAll]=useState(false),[report,setReport]=useState<Scores|null>(null),[reportError,setReportError]=useState(false),[reportTry,setReportTry]=useState(0)
  const [limit,setLimit]=useState(40),[mobile,setMobile]=useState<'list'|'detail'>('detail'),[initialAsk,setInitialAsk]=useState('')
  const [missingOnly,setMissingOnly]=useState(false)
  const [restoreRound,setRestoreRound]=useState('')
  const listNode=useRef<HTMLDivElement>(null)
  function restoreMatch(fid:string) {
    const year=fid.match(/^(20\d\d-\d\d)_/)?.[1]
    if(!year)return
    const saved=route().week
    setSeason(year);setChosen(fid);setWeek(typeof saved==='string'?saved:'')
    setRestoreRound(typeof saved==='string'?'':fid);setMobile('detail')
    setMissingOnly(false);setQuery('')
  }
  useEffect(()=>{
    const fn=()=>{const r=route();setNav(r);restoreMatch(r.id)}
    window.addEventListener('hashchange',fn)
    return ()=>window.removeEventListener('hashchange',fn)
  },[])
  useEffect(()=>{
    const c=new AbortController();setFailure('')
    fetchBoard(c.signal).then(d=>{
      if(c.signal.aborted) return
      const first=d.fixtures.find(f=>f.id===route().id)||d.fixtures.find(f=>f.id===d.default_id)||d.fixtures[0]
      setData(d)
      if(route().id)restoreMatch(route().id)
      else if(first){setSeason(first.season);setWeek(weekOf(first));setChosen(first.id)}
    }).catch(()=>{if(!c.signal.aborted) setFailure('比赛数据加载失败。请确认本地服务或静态数据包可访问。')})
    return ()=>c.abort()
  },[attempt])
  const matches=useMemo(()=>{
    const all=new Map((data?.fixtures||[]).map(f=>[f.id,f]))
    for(const f of Object.values(archives).flat())if(!all.has(f.id)||all.get(f.id)?.partial)all.set(f.id,f)
    return Array.from(all.values())
  },[data,archives])
  const id=nav.id||chosen
  const summary=useMemo(()=>matches.find(f=>f.id===id),[matches,id])
  const extra=detail?.id===id?detail:null
  const f=extra||(summary?.partial?undefined:summary)
  useEffect(()=>{
    const match=f||summary
    if(restoreRound&&match?.id===restoreRound){setWeek(weekOf(match));setRestoreRound('')}
  },[restoreRound,summary,f])
  const seasons=useMemo(()=>Array.from(new Set([...(data?.archive_seasons||data?.available_seasons||[]),...matches.map(x=>x.season),...(season?[season]:[])])).sort().reverse(),[matches,data,season])
  const needsAskSchedule=nav.view==='ask'&&isStatic()&&data?.compact
  const requestSeason=needsAskSchedule?data.health?.season||season:season
  const needSeason=Boolean(data&&requestSeason&&!archives[requestSeason]&&(!data.fixtures.some(x=>x.season===requestSeason)||
    data.compact&&(browseAll||needsAskSchedule||(id&&isStatic()&&!data.fixtures.some(x=>x.id===id&&!x.partial)))))
  useEffect(()=>{
    if(!needSeason){setBusy(false);setSeasonError('');return}
    const c=new AbortController();setBusy(true);setSeasonError('')
    fetchSeasonFixtures(requestSeason,c.signal).then(rows=>{if(!c.signal.aborted)setArchives(old=>({...old,[requestSeason]:rows}))}).catch(()=>{if(!c.signal.aborted)setSeasonError('这个赛季的赛程加载失败。')}).finally(()=>{if(!c.signal.aborted)setBusy(false)})
    return ()=>c.abort()
  },[requestSeason,needSeason,retrySeason])
  useEffect(()=>{
    if(!data?.compact||nav.view!=='report'||report)return
    const c=new AbortController();setReportError(false)
    fetch(isStatic()?'./report.json':'/api/report',{signal:c.signal}).then(async r=>{if(!r.ok)throw new Error();return r.json()}).then(scores=>{if(!c.signal.aborted)setReport(scores)}).catch(()=>{if(!c.signal.aborted)setReportError(true)})
    return ()=>c.abort()
  },[data,nav.view,report,reportTry])
  useEffect(()=>{
    setDetail(null);setDetailError('')
    if(!data||!id||isStatic()||(data.compact&&data.fixtures.some(m=>m.id===id&&!m.partial))) return
    const c=new AbortController()
    fetch('/api/fixtures/'+encodeURIComponent(id),{signal:c.signal}).then(async r=>{if(!r.ok)throw new Error();return r.json()}).then(d=>{
      if(c.signal.aborted)return
      const entry:Fixture=d.fixture||d
      if(entry.id!==id)throw new Error()
      setDetail(entry)
    }).catch(()=>{if(!c.signal.aborted)setDetailError('无法更新比赛详情。')})
    return ()=>c.abort()
  },[id,data,retryDetail])
  const inSeason=matches.filter(x=>x.season===season).sort((a,b)=>a.date.localeCompare(b.date)||String(a.kickoff_utc||'').localeCompare(String(b.kickoff_utc||''))||a.id.localeCompare(b.id))
  const weeks=Array.from(new Set([...(data?.week_catalog?.[season]||[]),...inSeason.map(weekOf)])).sort((a,b)=>a.localeCompare(b,'zh-CN',{numeric:true}))
  const q=query.trim().toLowerCase()
  const missingIds=new Set(data?.health?.overdue_ids||[])
  const filtered=inSeason.filter(x=>(!missingOnly||missingIds.has(x.id))&&(q||!week||weekOf(x)===week)&&(!q||[x.home,x.away,nameOf(x,'home'),nameOf(x,'away')].some(t=>t.toLowerCase().includes(q))))
  useEffect(()=>{
    const list=listNode.current
    const button=list?.querySelector<HTMLButtonElement>('button.active')
    if(!list||!button||list.clientHeight===0||list.scrollHeight<=list.clientHeight)return
    const distance=button.getBoundingClientRect().top-list.getBoundingClientRect().top
    if(distance<0||distance+button.offsetHeight>list.clientHeight)list.scrollTop+=distance
  },[id,week,season,data,mobile])
  function select(fid:string,selectedWeek=week) {
    const game=matches.find(m=>m.id===fid)
    const filter=game&&season===game.season?selectedWeek:game?weekOf(game):undefined
    const url='#/match/'+encodeURIComponent(fid)+(filter===undefined?'':'?week='+encodeURIComponent(filter))
    if(location.hash!==url)history.pushState(null,'',url)
    setNav(route());setInitialAsk('');restoreMatch(fid)
  }
  function toPage(page:View) {setInitialAsk('');if(id)setChosen(id);location.hash=page==='matches'?(id?'/match/'+encodeURIComponent(id):'/matches'):'/'+page}
  function selectSeason(s:string){setRestoreRound('');setBrowseAll(true);setMissingOnly(false);setSeason(s);setWeek('');setQuery('');setLimit(40);setMobile('list')}
  function changeWeek(value:string){setRestoreRound('');setBrowseAll(true);setMissingOnly(false);setWeek(value);setQuery('');setLimit(40);setMobile('list')}
  function jump(fid?:string) {
    const game=matches.find(m=>m.id===fid)
    if(!game)return
    setMissingOnly(false);setSeason(game.season);setWeek(weekOf(game));setQuery('');setLimit(40);select(game.id,weekOf(game))
  }
  const scores=data?.compact?report:data?.report
  const title=tabs.find(([key])=>key===nav.view)![1]
  useEffect(()=>{document.title=title+' · 英超观察台'},[title])
  return <><a className="skip" href="#content" onClick={e=>{e.preventDefault();document.getElementById('content')?.focus()}}>跳到主要内容</a>
    <header className="top"><div className="shell mast"><a href="#/matches" aria-label="英超观察台首页"><Logo/></a>
      <nav aria-label="主导航">{tabs.map(([k,label])=><a href={'#/'+k} key={k} aria-current={k===nav.view?'page':undefined} onClick={e=>{e.preventDefault();toPage(k)}}><i aria-hidden="true" className={'nav-icon '+k}/>{label}</a>)}</nav>
      <span className="edition">MATCHDESK <b>/</b> EPL</span></div></header>
    <main className="shell" id="content" tabIndex={-1}><div className="page-title"><div><div className="eyebrow"><span aria-hidden="true"/>英格兰足球超级联赛</div><h1>{title}</h1></div>
      {data&&<span className="updated">最新确认比分 <b>{fmtTime(data.health?.latest_confirmed_score_date??data.health?.last_scored_date??data.last_scored_date,true)}</b></span>}</div>
    {failure?<section role="alert" className="panel">{failure} <button className="plain" onClick={()=>setAttempt(attempt+1)}>重试</button></section>:!data?<p role="status" className="panel">正在加载英超比赛…</p>:nav.view==='report'?(scores?<Report r={scores} partitioned={Boolean(data.compact)} fixedSnapshot={data.fixed_snapshot_url} publicSnapshot={Boolean(data.public_snapshot)} openMatch={(fid,s)=>{setSeason(s);setWeek('');select(fid)}}/>:reportError?<section className="panel" role="alert">成绩加载失败。<button onClick={()=>setReportTry(n=>n+1)}>重试</button></section>:<p className="panel" role="status">正在加载模型成绩…</p>):nav.view==='ask'&&needSeason&&!seasonError?<p className="panel" role="status">正在载入当季赛程…</p>:nav.view==='ask'?<Ask key={id+initialAsk} data={{...data,fixtures:matches}} f={f||undefined} initial={initialAsk} select={select}/>:<>
      <section className="board-tools" aria-label="比赛入口与数据状态"><div className="board-jumps">
        <button className="jump-live" disabled={!data.current_id} onClick={()=>jump(data.current_id)}>看本轮 <span aria-hidden="true">↗</span></button>
        <button className="jump-replay" onClick={()=>jump(data.default_id)}>体验历史回放</button></div>
        {data.health&&<div className="health-line"><span>最近成功同步 <b>{data.health.last_sync_at?fmtTime(data.health.last_sync_at):'尚无本地同步记录'}</b></span>
        {data.operations&&<span>{data.operations.snapshot_at?'公开批次 '+fmtTime(data.operations.snapshot_at):
          '采集机 '+({online:'在线',offline:'心跳超时',unknown:'尚无心跳'}[data.operations.worker_status]||'待确认')}</span>}
        {data.operations?.snapshot_at&&Date.now()-Date.parse(data.operations.snapshot_at)>4*3600000&&<strong>公开批次已超过 4 小时</strong>}
        {data.operations?.upload_sha&&data.operations.upload_sha!==data.operations.deployed_sha&&<span>上传批次等待上线核实</span>}
        <button aria-pressed={missingOnly} onClick={()=>{selectSeason(data.health!.season);setMissingOnly(!missingOnly)}}>待补 {data.health.overdue_count} 场</button>
        {Boolean(data.health.schedule_conflict_count||data.health.schedule_unconfirmed_count)&&<span>开球待核实 {(data.health.schedule_conflict_count||0)+(data.health.schedule_unconfirmed_count||0)} 场</span>}
        {data.health.conflict_count>0&&<span>其中 {data.health.conflict_count} 场比分待核实</span>}</div>}
        {(Boolean(data.health?.sources?.length)||data.operations?.publication_status==='credentials_missing')&&<details className="source-health"><summary>来源与采集状态</summary>
          <p>已核实赛程连续结算至 {data.health?.settled_through?fmtTime(data.health.settled_through,true):"尚无完整记录"}。</p>
          {data.health?.sources?.map(source=><p key={source.provider}>{source.provider} · {source.status==='ok'?'已连通':source.status==='never'?'尚无连通记录':'最近请求失败'} · 上次成功 {fmtTime(source.last_success_at)}</p>)}
          {data.operations?.publication_status==='credentials_missing'&&<p>{isStatic()?'本批次记录：公开见证与发布等待 GitHub 授权。':'公开见证与发布等待 GitHub 授权，本机继续采集。'}</p>}
          {data.operations?.heartbeat_at&&<p>批次内采集心跳 {fmtTime(data.operations.heartbeat_at)}</p>}
          {data.operations?.next_cutoff&&<p>下个冻结截止 {fmtTime(data.operations.next_cutoff)}</p>}
          {!!data.health?.overdue_count&&<p>已核实开球超过 24 小时仍有比分待补。</p>}
        </details>}</section>
      <div className="mobile-tabs"><button aria-pressed={mobile==='list'} onClick={()=>setMobile('list')}>赛程列表</button><button aria-pressed={mobile==='detail'} onClick={()=>setMobile('detail')}>比赛详情</button></div>
      <div className={'workspace show-'+mobile}>
      <aside className="schedule"><div className="schedule-top"><div className="section-head"><h2>联赛赛程</h2><span>{season}</span></div>
        <div className="filter-row"><label>赛季<select aria-label="赛季" value={season} onChange={e=>selectSeason(e.target.value)}>{seasons.map(s=><option key={s}>{s}</option>)}</select></label>
          <label>比赛周<select aria-label="比赛周" value={week} onChange={e=>changeWeek(e.target.value)}><option value="">全部比赛周</option>{weeks.map(w=><option key={w}>{w}</option>)}</select></label></div>
        <label className="search"><i aria-hidden="true"/><input aria-label="搜索球队" placeholder="搜索球队 · 中文或英文" value={query} onChange={e=>{setBrowseAll(true);setQuery(e.target.value);setLimit(40)}}/>{query&&<button aria-label="清除搜索" onClick={()=>setQuery('')}>×</button>}</label>
      </div>
        <div className="list-meta"><span>{missingOnly?'超过开球时间 24 小时仍待补':q?'搜索结果':week||'全部赛程'}</span><b>{filtered.length} 场</b></div>
        {busy?<p className="list-note" role="status">正在加载赛程…</p>:seasonError?<p className="list-note" role="alert">{seasonError}<button onClick={()=>setRetrySeason(retrySeason+1)}>重试</button></p>:!filtered.length?<p className="list-note">没有匹配的比赛。</p>:<div className="fixtures" ref={listNode}>{filtered.slice(0,limit).map(x=><button className={'fixture '+(id===x.id?'active':'')} key={x.id} aria-pressed={id===x.id} onClick={()=>select(x.id)}>
          <span className="fixture-meta">{fmtTime(x.kickoff_utc||x.date)}<span>{fixtureStatus(extra?.id===x.id?extra:x)}</span></span>
          <span className="fixture-teams"><span><TeamMark team={x.home} small/>{nameOf(x,'home')}</span><span><TeamMark team={x.away} small/>{nameOf(x,'away')}</span></span>
          <ListChance p={extra?.id===x.id?extra.prediction:x.prediction}/></button>)}
          {filtered.length>limit&&<button className="load" onClick={()=>setLimit(limit+40)}>显示更多比赛</button>}</div>}
      </aside>
      <div className="detail-column">
        {detailError&&<p role="status" className="notice">{detailError} <button onClick={()=>setRetryDetail(retryDetail+1)}>重试</button></p>}
        {f?<><Match key={f.id} f={f} openAsk={q=>{setChosen(f.id);setInitialAsk(q);location.hash='/ask'}}/>
          {(f.source_conflict||missingIds.has(f.id)||f.schedule_status==='conflict'||f.schedule_status==='unconfirmed')&&<details className="panel provenance"><summary>本场来源与待核事项</summary>
            <p>{f.source_conflict?'两家来源的终场比分有分歧，暂停结算。':missingIds.has(f.id)?'开球已超过 24 小时，等待来源确认终场比分。':f.freeze_note||'开球时间待核实。'}</p>
            {['openfootball','openligadb'].map(provider=>{const observation=f.score_sources?.[provider], last=f.season===data.health?.season?data.health.sources?.find(s=>s.provider===provider)?.last_success_at:null
              return <p key={provider}>{provider} · {observation?.score?('终场 '+observation.score.join('–')):'尚无确认终场比分'} · 本场最近观测 {fmtTime(observation?.observed_at)}{last?' · 联赛最近同步 '+fmtTime(last):''}</p>})}</details>}</>:<section className="panel">{isStatic()&&id&&!busy&&!needSeason?'静态演示未包含这场比赛，请从赛程列表选择已收录样本。':id&&!detailError?'正在读取比赛详情…':'请选择一场比赛。'}</section>}
      </div></div></>}
    </main><footer className="shell"><span>英超观察台 · 常规时间含补时</span><a href="./about.html">AI、隐私与反馈</a><span>比分来源 <a href="https://github.com/openfootball/england">openfootball · CC0</a> / <a href="https://www.openligadb.de/en/license">OpenLigaDB · ODbL 1.0</a></span></footer></>
}
createRoot(document.getElementById('root')!).render(<App/>)
