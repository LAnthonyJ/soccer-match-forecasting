import { useEffect, useLayoutEffect, useRef, useState, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { FanList, Featured, WeekTools, featuredGame, type Filters } from './FanBoard'
import { type FanBoard, type Fixture, type Report as Scores, fetchFanBoard, fetchFanRound, fetchFanFixture, isStatic, weekOf } from './data'
import { fanPhase, localZoneLabel } from './fanFixture'
import './base.css'
import './FanBoard.css'

const Research=lazy(()=>import('./Report').then(m=>({default:m.Report})))
const loadMatch=()=>import('./Match')
const Match=lazy(()=>loadMatch().then(m=>({default:m.Match})).catch(()=>({
  default:()=> <div role="alert" className="fan-empty">比赛页面暂时无法载入。 <button onClick={()=>window.location.reload()}>重新加载</button></div>
})))
type View='matches'|'match'|'report'|'ask'
interface Nav {view:View;id:string;params:URLSearchParams}
interface ReturnPoint {href:string;y:number;focus:string}
function parse(hash:string):Nav {
  const [path,q='']=hash.replace(/^#\/?/,'').split('?')
  const [page,id]=path.split('/')
  let decoded=''
  try{decoded=decodeURIComponent(id||'')}catch{/* an invalid link receives the same missing-fixture view */}
  return {view:page==='match'||page==='ask'||page==='report'?page:'matches',id:decoded,params:new URLSearchParams(q)}
}
function boardHref(f:Filters){
  const p=new URLSearchParams({season:f.season,week:f.week})
  if(f.q)p.set('q',f.q)
  if(f.date)p.set('date',f.date)
  return '#/matches?'+p.toString()
}
function ResearchPage({data,open}:{data:FanBoard;open:(id:string)=>void}) {
  const [report,setReport]=useState<Scores|null>(null),[failed,setFailed]=useState(false),[retry,setRetry]=useState(0)
  useEffect(()=>{
    const c=new AbortController()
    setFailed(false)
    fetch(isStatic()?'./report.json':'/api/report',{signal:c.signal}).then(async r=>{
      if(!r.ok)throw new Error();return r.json()
    }).then(r=>{if(!c.signal.aborted)setReport(r)}).catch(()=>{if(!c.signal.aborted)setFailed(true)})
    return ()=>c.abort()
  },[retry])
  if(failed)return <div role="alert" className="fan-empty">成绩暂时无法读取。 <button onClick={()=>setRetry(n=>n+1)}>重试</button></div>
  if(!report)return <p role="status" className="fan-empty">正在读取研究数据…</p>
  return <Suspense fallback={<p role="status">正在载入成绩页…</p>}><Research r={report} partitioned publicSnapshot={data.public_snapshot} fixedSnapshot={data.fixed_snapshot_url||undefined} openMatch={open}/></Suspense>
}
function App(){
  const [nav,setNav]=useState(()=>parse(location.hash))
  const [data,setData]=useState<FanBoard|null>(null),[boardError,setBoardError]=useState(false),[boardTry,setBoardTry]=useState(0)
  const [round,setRound]=useState<{key:string;games:Fixture[]}|null>(null),[roundError,setRoundError]=useState(''),[roundTry,setRoundTry]=useState(0)
  const [detail,setDetail]=useState<Fixture|null>(null),[detailError,setDetailError]=useState(''),[detailTry,setDetailTry]=useState(0)
  const [now,setNow]=useState(()=>Date.now())
  const [desktop,setDesktop]=useState(()=>window.matchMedia('(min-width: 821px)').matches)
  const pendingScroll=useRef<ReturnPoint|null>(null)
  const listRef=useRef<HTMLDivElement>(null)
  const detailHeading=useRef<HTMLDivElement>(null)
  const backPoint=useRef<ReturnPoint|null>(history.state?.fanOrigin||null)
  const visit=(hash:string,settings:{replace?:boolean;origin?:ReturnPoint|null}={})=>{
    const state={fanOrigin:settings.origin||null}
    if(settings.replace)history.replaceState(state,'',hash);else history.pushState(state,'',hash)
    backPoint.current=state.fanOrigin
    setNav(parse(location.hash))
  }
  useEffect(()=>{
    const onHistory=()=>{
      const current=parse(location.hash)
      backPoint.current=history.state?.fanOrigin||null
      pendingScroll.current=current.view==='matches'?history.state?.fanReturn||null:null
      setNav(current)
    }
    window.addEventListener('hashchange',onHistory)
    window.addEventListener('popstate',onHistory)
    const media=window.matchMedia('(min-width: 821px)')
    const resize=()=>setDesktop(media.matches)
    media.addEventListener('change',resize)
    const clock=window.setInterval(()=>setNow(Date.now()),15000)
    return ()=>{window.removeEventListener('hashchange',onHistory);window.removeEventListener('popstate',onHistory);media.removeEventListener('change',resize);clearInterval(clock)}
  },[])
  useEffect(()=>{
    const c=new AbortController();setBoardError(false)
    fetchFanBoard(c.signal).then(b=>{if(!c.signal.aborted)setData(b)}).catch(()=>{if(!c.signal.aborted)setBoardError(true)})
    return ()=>c.abort()
  },[boardTry])
  const fromBoard=nav.view==='match'&&backPoint.current?parse(backPoint.current.href):nav
  const id=nav.view==='match'?nav.id:nav.view==='ask'?data?.current_id||'':''
  useEffect(()=>{if(id)void loadMatch().catch(()=>{})},[id])
  const card=data?.fixtures.find(f=>f.id===id)
  const selectedDetail=detail?.id===id?detail:null
  const summaryOnly=card&&['finished','conflict','waiting'].includes(fanPhase(card,now))
  const displayFixture=summaryOnly?card:selectedDetail
  const selectedWeek=selectedDetail||card
  const defaultSeason=data?.current_season||''
  const season=fromBoard.params.get('season')||selectedWeek?.season||id.match(/^(\d{4}-\d{2})_/)?.[1]||defaultSeason
  const catalog=data?.week_catalog[season]||[]
  const defaultWeek=season===defaultSeason?data?.current_week:catalog.at(-1)
  const week=fromBoard.params.get('week')||(selectedWeek?weekOf(selectedWeek):'')||defaultWeek||''
  const filters:Filters={season,week,q:fromBoard.params.get('q')||'',date:fromBoard.params.get('date')||''}
  const roundKey=season+'|'+week
  const currentRound=!!data&&season===data.current_season&&week===data.current_week
  const games=currentRound?data.fixtures:round?.key===roundKey?round.games:[]
  const showRound=nav.view!=='report'
  const matchView=nav.view==='match'||nav.view==='ask'
  const showList=showRound&&(!matchView||desktop)
  const roundKnown=!matchView||Boolean(selectedWeek||fromBoard.params.get('week'))
  const needsRound=!!data&&showList&&roundKnown&&!currentRound&&!!week&&catalog.includes(week)
  useEffect(()=>{
    if(!needsRound)return
    const c=new AbortController();setRoundError('')
    fetchFanRound(season,week,c.signal).then(rows=>{if(!c.signal.aborted)setRound({key:roundKey,games:rows})})
      .catch(()=>{if(!c.signal.aborted)setRoundError(roundKey)})
    return ()=>c.abort()
  },[needsRound,roundKey,roundTry])
  useEffect(()=>{
    if(!id||!data||summaryOnly)return
    const c=new AbortController();setDetailError('')
    fetchFanFixture(id,c.signal).then(game=>{
      if(game.id!==id)throw new Error()
      if(!c.signal.aborted)setDetail(game)
    }).catch((e:unknown)=>{if(!c.signal.aborted)setDetailError(e instanceof Error&&e.message==='404'?'找不到这场比赛。':'比赛详情暂时无法读取。')})
    return ()=>c.abort()
  },[id,data,summaryOnly,detailTry])
  useLayoutEffect(()=>{
    const restore=pendingScroll.current
    if(nav.view!=='matches'||!restore||!data||(!currentRound&&round?.key!==roundKey))return
    const row=listRef.current?.querySelector<HTMLButtonElement>('[data-fixture-id="'+CSS.escape(restore.focus)+'"]')
    row?.focus({preventScroll:true})
    window.scrollTo(0,restore.y)
    pendingScroll.current=null
  },[nav,data,roundKey,round,currentRound])
  useEffect(()=>{
    document.title=(nav.view==='report'?'模型成绩':displayFixture?((displayFixture.home_zh||displayFixture.home)+' 对 '+(displayFixture.away_zh||displayFixture.away)):'英超赛程')+' · 英超观察台'
  },[nav.view,displayFixture])
  useEffect(()=>{
    if(id&&window.matchMedia('(max-width: 820px)').matches){
      detailHeading.current?.focus({preventScroll:true})
      window.scrollTo(0,0)
    }
  },[id])
  function change(values:Partial<Filters>,replace=false){
    const updated={...filters,...values}
    visit(boardHref(updated),{replace:replace&&nav.view==='matches'})
  }
  function reset(){
    if(data)visit(boardHref({season:data.current_season,week:data.current_week,date:'',q:''}))
  }
  function open(fid:string){
    if(!fid||fid===id)return
    let origin:ReturnPoint|null=null
    if(nav.view==='matches'){
      origin={href:boardHref(filters),y:window.scrollY,focus:fid}
      history.replaceState({fanReturn:origin},'',origin.href)
    }else if(nav.view==='match')origin=backPoint.current
    visit('#/match/'+encodeURIComponent(fid),{origin,replace:nav.view==='match'&&!!origin})
  }
  function back(){
    if(backPoint.current){pendingScroll.current=backPoint.current;history.back()}
    else visit(boardHref(filters))
  }
  const spotlight=featuredGame(games,now)
  const relevantSpotlight=spotlight&&!filters.q&&!filters.date
  const loading=needsRound&&round?.key!==roundKey&&roundError!==roundKey
  const fanLayout=data&&showRound
  const reportView=nav.view==='report'
  return <div className="fan-app">
    <a className="skip" href="#fan-content" onClick={e=>{e.preventDefault();document.getElementById('fan-content')?.focus()}}>跳到主要内容</a>
    <header className="fan-header"><div className="fan-shell fan-mast"><a className="fan-brand" href="#/matches" aria-label="英超观察台首页">
      <i className="fan-brand-mark" aria-hidden="true"><b/><b/><b/></i>英超观察台</a>
      <a href="#/matches" className="fan-nav-active">赛程</a>
      <span className="fan-zone">{localZoneLabel()}</span></div></header>
    <main id="fan-content" tabIndex={-1} className={'fan-shell fan-main'+(matchView?' is-match':'')}>
      {!data||boardError?<section className="fan-loading" role={boardError?'alert':'status'}><h1>英超赛程</h1>
        <p>{boardError?'赛程暂时无法读取，请检查网络后重试。':'正在读取本轮赛程…'}</p>{boardError&&<button onClick={()=>setBoardTry(n=>n+1)}>重试</button>}</section>:null}
      {fanLayout&&<>{showList&&<div className="fan-pagehead">
        <div><h1>英超赛程</h1><label className="fan-season"><select aria-label="赛季" value={season} onChange={e=>{
          const s=e.target.value;change({season:s,week:s===data.current_season?data.current_week:data.week_catalog[s]?.at(-1)||'',date:'',q:''})
        }}>{Object.keys(data.week_catalog).sort().reverse().map(s=><option value={s} key={s}>{s.replace('-',' / ')} 赛季</option>)}</select></label></div>
        <WeekTools data={data} filters={filters} change={change} reset={reset}/>
      </div>}
      <div className={'fan-workspace'+(matchView?' with-match':relevantSpotlight?' with-preview':'')}>
        {showList&&<div className="fan-list-col" ref={listRef}>
          <FanList data={data} games={games} filters={filters} selected={id} loading={loading} error={roundError===roundKey?'赛程暂时无法读取。':''}
            change={change} open={open} retry={()=>setRoundTry(n=>n+1)} now={now}/>
        </div>}
        {matchView?<div className="fan-detail-col" ref={detailHeading} tabIndex={-1}>
          <button type="button" className="fan-back" data-testid="back-to-fixtures" onClick={back}><span aria-hidden="true">‹</span> 返回赛程</button>
          {displayFixture?<Suspense fallback={<p role="status" className="fan-empty">正在载入比赛页面…</p>}><Match key={id} f={displayFixture}/></Suspense>:detailError||!id?<div role="alert" className="fan-empty">{detailError||'无法识别这场比赛。'} <button onClick={()=>setDetailTry(n=>n+1)}>重试</button></div>:
            <p role="status" className="fan-empty">正在读取比赛详情…</p>}
        </div>:relevantSpotlight&&<Featured f={spotlight} open={open}/>}
      </div></>}
      {data&&reportView&&<><div className="fan-pagehead"><h1>模型成绩</h1><a href="#/matches">返回赛程</a></div><ResearchPage data={data} open={open}/></>}
    </main>
    <footer className="fan-shell fan-footer"><span>英超观察台</span><a href="./about.html">数据来源与方法 <span aria-hidden="true">↗</span></a></footer>
  </div>
}
createRoot(document.getElementById('root')!).render(<App/>)
