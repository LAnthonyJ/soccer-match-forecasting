import { useEffect, useMemo, useState } from 'react'
import { type Component, type HistoryRow, type MarketRow, type Outcome, type Report as Scores, type Triple,
  fmtTime, isStatic, num, pct, reasons, statusLabels } from './data'
import './style.css'
import './ReportStyles.css'

const components:Component[] = ['elo','dc','tree','a']
const modelNames:Record<Component,string> = {elo:'Elo',dc:'Dixon–Coles',tree:'梯度提升树',a:'融合模型 A'}
const sides:Record<Outcome,string> = {H:'主胜',D:'平局',A:'客胜'}
const outcomes:Outcome[] = ['H','D','A']
const cohortNames = {history:'历史回放',official:'正式市场评测',pilot:'前瞻试运行',independent:'独立 A · 全部前瞻场次'}
type Cohort = keyof typeof cohortNames
type OpenMatch = (id:string,season:string)=>void
interface Bin {lo:number; hi:number; n:number; p:number|null; observed:number|null}
type Calibration = Record<Component,Record<Outcome,Bin[]>>
interface Metric {n:number; missing:number; log_loss:number|null; brier:number|null; accuracy:number|null}
type ModelScores = Record<Component,Metric>
interface Diagnostics {
  n:number; total:number; overall:ModelScores; calibration:Calibration
  seasons:{season:string; n:number; total:number; scores:ModelScores; calibration:Calibration}[]
}
const reasonLabels:Record<string,string> = {...reasons, pending_result:'等待赛果', complete:'数据完整',
  missing_component:'组件概率缺失', quota_reserved:'额度已到预留线', no_event:'未收到同期报价',
  duplicate_fixture:'赛程重复', late_receipt:'报价超过截止时间', late_generation:'生成超过截止时间',
  no_witness:'缺少公开时间见证', no_odds:'缺少同期赔率', source_conflict:'比分待核实',
  before_registration:'注册前的场次', duplicate_capture:'采集记录重复', model_version_changed:'模型版本已变更'}
const reasonName = (reason:string) => reasonLabels[reason] || reason

function MarketReport({r}:{r:Scores}) {
  const [cohort,setCohort] = useState<'official'|'pilot'>('official')
  const official = cohort==='official'
  const seasons = (official?r.seasons:r.pilot?.seasons) || []
  const independent = official?r.independent:r.pilot?.independent
  const market = official?r:r.pilot
  return <>
    <section className="report-banner"><div><small>T−24h · 市场对照</small><h2>{statusLabels[r.status]}</h2>
      <p>开球前 24 小时的冻结预测，与同一场的去水市场概率比较。</p></div>
      <div className="report-stat"><b>{r.n_pairs.toLocaleString()}</b><small>正式有效配对</small></div></section>
    <section className="panel"><header className="section-head"><h3>前瞻评测</h3>
      <div className="rp-switch" role="group" aria-label="评测赛季组">{(['official','pilot'] as const).map(k=>
        <button key={k} aria-pressed={cohort===k} onClick={()=>setCohort(k)}>{k==='official'?'正式评测':'试运行'}</button>)}</div></header>
      <dl className="metrics rp-metrics">
        <div><dt>已核验配对</dt><dd>{official?r.n_pairs:r.pilot?.n_pairs||0} 场</dd></div>
        {official&&<><div><dt>对数损失改善量</dt><dd>{num(r.delta,4)}</dd></div>
          <div><dt>95% 置信区间</dt><dd>{r.ci?r.ci.map(v=>num(v,4)).join(' 至 '):'样本不足'}</dd></div>
          <div><dt>等待公开审计</dt><dd>{r.verification?.pending_pairs||0} 场</dd></div></>}
      </dl>
      {independent&&<div className="cohort-card"><h4>独立模型 A 前瞻成绩</h4>
        <p>已核验 {independent.n_scored} 场 / 注册期赛程 {independent.eligible} 场，其中 {independent.finished} 场已完场。</p>
        <dl className="metrics rp-metrics"><div><dt>命中率</dt><dd>{pct(independent.scores.accuracy)}</dd></div>
        <div><dt>对数损失</dt><dd>{num(independent.scores.log_loss,4)}</dd></div>
        <div><dt>Brier</dt><dd>{num(independent.scores.brier,4)}</dd></div></dl>
        <details><summary>独立模型覆盖与缺项</summary><p>赛程覆盖 {pct(independent.coverage)} · 完场覆盖 {pct(independent.finished_coverage)}</p>
        {Object.entries(independent.missing).map(([k,n])=><p key={k}>{reasonName(k)} · {n} 场</p>)}</details></div>}
      {market?.scores&&<div className="table-wrap"><table><thead><tr><th>同场配对</th><th>命中率</th><th>对数损失</th><th>Brier</th></tr></thead>
        <tbody>{(['a','market','b'] as const).map(k=><tr key={k}><th>{{a:'模型 A',market:'市场',b:'融合 B'}[k]}</th>
        <td>{pct(market.scores?.[k]?.accuracy)}</td><td>{num(market.scores?.[k]?.log_loss,4)}</td><td>{num(market.scores?.[k]?.brier,4)}</td></tr>)}</tbody></table></div>}
      {seasons.length>0?<div className="table-wrap"><table><thead><tr><th>赛季</th><th>赛程</th><th>已核验配对</th><th>覆盖率</th><th>模型 B 损失</th><th>市场损失</th></tr></thead>
        <tbody>{seasons.map(s=><tr key={s.season}><th>{s.season}</th><td>{s.total}</td><td>{s.paired}</td><td>{pct(s.coverage)}</td><td>{num(s.model_loss)}</td><td>{num(s.market_loss)}</td></tr>)}</tbody></table></div>:
        <p className="muted">{official?'尚无符合协议的正式赛季成绩。':'尚无试运行赛季记录。'}</p>}
      {!official&&<p className="muted">试运行用于检查采集和公开见证。领先市场的结论使用正式赛季。</p>}
      <a className="ledger" href={isStatic()?'./ledger.json':'/api/ledger'} target="_blank" rel="noopener">查看赛前预测账本（JSON）↗</a>
      <details className="protocol"><summary>评测门槛与缺失场次</summary><p>冻结后的两个完整赛季，各季覆盖率至少 90%。市场减模型的平均对数损失至少为 0.005，比赛周分块重采样的 95% 区间下界须大于零。</p>
        {Object.entries(market?.missing||{}).map(([k,n])=><p key={k}>{reasonName(k)} · {n} 场</p>)}</details>
    </section>
  </>
}

function PilotReceipt({r,openMatch,publicSnapshot,fixedSnapshot}:{r:Scores;openMatch:OpenMatch;publicSnapshot:boolean;fixedSnapshot?:string}) {
  const paired = [...(r.pilot?.rows||[])].filter(row=>row.status==='paired' && row.evidence && row.a && row.b && row.market
    && (outcomes.includes(row.actual as Outcome)||[0,1,2].includes(Number.isInteger(row.actual)?Number(row.actual):-1)))
    .sort((a,b)=>(a.kickoff||'').localeCompare(b.kickoff||'')||a.id.localeCompare(b.id))[0]
  const first = paired || r.pilot?.independent?.rows.find(row=>row.status==='scored'&&row.a&&row.evidence)
  const index=first ? (typeof first.actual==='number'?first.actual:outcomes.indexOf(first.actual as Outcome)) : -1
  return <section className="panel"><header className="section-head"><h3>首场公开核验</h3><span>前瞻试运行</span></header>
    {first?<><p><button className="rp-match" onClick={()=>openMatch(first.id,first.season)}>{first.home} vs {first.away} ↗</button>
      {' · '}{first.kickoff?fmtTime(first.kickoff):first.season} · {sides[outcomes[index]]}</p>
      {first.evidence&&/^https:\/\/github\.com\//.test(first.evidence)&&<a className="ledger" href={first.evidence} target="_blank" rel="noopener">查看本场赛前公开见证 ↗</a>}
      <div className="table-wrap"><table><thead><tr><th>本场预测</th><th>对赛果的概率</th><th>对数损失</th><th>Brier</th></tr></thead>
        <tbody>{(['a','market','b'] as const).filter(k=>first[k]).map(k=>{const probs=first[k]!
          return <tr key={k}><th>{{a:'独立模型 A',market:'市场基准',b:'融合模型 B'}[k]}</th><td>{pct(probs[index])}</td>
            <td>{num(-Math.log(Math.max(probs[index],1e-15)),4)}</td><td>{num(probs.reduce((sum,v,i)=>sum+(v-Number(i===index))**2,0),4)}</td></tr>})}</tbody></table></div>
      </>:<p className="muted">待验收。首场比赛完成赛前公开见证、赛果结算和独立核验后，这里将显示见证与单场评分。</p>}
    {publicSnapshot&&<div className="rp-files" aria-label="复算数据下载"><a download href={fixedSnapshot||'./snapshot.json'}>{fixedSnapshot?'本批次固定预测快照 JSON':'预测快照 JSON'} ↓</a>
      <a download href="./merged-seasons.json">合并赛季数据 · ODbL ↓</a><a download href="./openligadb.json">OpenLigaDB 数据 ↓</a></div>}
    <a className="ledger" href="https://github.com/LAnthonyJ/worldcup_prediction/blob/epl-v1/docs/EVALUATION.md" target="_blank" rel="noopener">复算步骤与评测协议 ↗</a>
  </section>
}

function CalibrationChart({bins,side}:{bins:Bin[];side:Outcome}) {
  const points = bins.flatMap(b=>b.n && b.p!==null && b.observed!==null ? [{p:b.p,f:b.observed,n:b.n}] : [])
  const x = (p:number)=>40+260*p, y = (f:number)=>148-130*f
  return <>
    <svg className="rp-chart" viewBox="0 0 328 188" role="img" aria-label={sides[side]+'校准图，横轴为平均预测概率，纵轴为实际频率，详细数值见下表'}>
      {[0,.25,.5,.75,1].map(v=><g key={v}><path className="rp-grid" d={'M40 '+y(v)+'H300 M'+x(v)+' 18V148'}/>
        <text x={x(v)} y="165" textAnchor="middle">{v*100}%</text><text x="32" y={y(v)+3} textAnchor="end">{v*100}%</text></g>)}
      <text x="40" y="11">实际频率</text><text x="170" y="182" textAnchor="middle">预测概率</text>
      <path className="rp-diagonal" d="M40 148L300 18"/>
      <polyline className="rp-series" points={points.map(p=>x(p.p)+','+y(p.f)).join(' ')}/>
      {points.map(p=><circle className="rp-dot" key={p.p} cx={x(p.p)} cy={y(p.f)} r="3.5"><title>{pct(p.p)+' 预测 · '+pct(p.f)+' 实际 · '+p.n+' 场'}</title></circle>)}
    </svg>
    <details className="rp-bins"><summary>{sides[side]}分箱明细</summary><div className="table-wrap"><table>
      <thead><tr><th>概率区间</th><th>平均预测</th><th>实际频率</th><th>场数</th></tr></thead>
      <tbody>{bins.map(b=><tr key={b.lo}><th>{Math.round(b.lo*100)}–{Math.round(b.hi*100)}%</th><td>{pct(b.p)}</td><td>{pct(b.observed)}</td><td>{b.n}</td></tr>)}</tbody>
    </table></div></details>
  </>
}

function HistorySummary({r}:{r:Scores}) {
  const h=r.history as Scores['history'] & {diagnostics?:Diagnostics}
  const d=h.diagnostics
  const [season,setSeason]=useState(''), [model,setModel]=useState<Component>('a'), [side,setSide]=useState<Outcome>('D')
  const [metric,setMetric]=useState<'log_loss'|'brier'|'accuracy'>('log_loss')
  const active=d?.seasons.find(s=>s.season===season)
  const scores=active?.scores||d?.overall, calibration=active?.calibration||d?.calibration
  return <>
    <section className="panel"><header className="section-head"><h3>模型对照</h3><span>{(active?.n??d?.n??h.n).toLocaleString()} 场共同样本</span></header>
      <div className="rp-toolbar"><label>诊断赛季<select value={season} onChange={e=>setSeason(e.target.value)}><option value="">全部历史赛季</option>{[...h.seasons].reverse().map(s=><option key={s.season}>{s.season}</option>)}</select></label></div>
      {scores?<div className="table-wrap"><table><thead><tr><th>模型</th><th>对数损失 ↓</th><th>Brier ↓</th><th>命中率</th><th>共同样本</th><th>该模型缺报</th></tr></thead>
        <tbody>{components.map(k=><tr key={k}><th>{modelNames[k]}</th><td>{num(scores[k].log_loss,4)}</td><td>{num(scores[k].brier,4)}</td><td>{pct(scores[k].accuracy)}</td><td>{scores[k].n}</td><td>{scores[k].missing}</td></tr>)}</tbody></table></div>:
        <p className="muted">此历史报告仅提供逐季汇总。使用新版训练命令可生成四模型诊断。</p>}
    </section>
    {calibration&&<section className="panel"><header className="section-head"><h3>概率校准</h3><span>{season||'全部历史赛季'}</span></header>
      <div className="rp-calibration"><div className="rp-cal-controls"><label>校准模型<select value={model} onChange={e=>setModel(e.target.value as Component)}>
        {components.map(k=><option key={k} value={k}>{modelNames[k]}</option>)}</select></label>
        <div className="rp-switch" role="group" aria-label="校准赛果">{outcomes.map(k=><button key={k} aria-pressed={side===k} onClick={()=>setSide(k)}>{sides[k]}</button>)}</div>
        <p className="muted">虚线表示预测概率等于实际频率。点高于虚线，表示该区间内{ sides[side] }出现得比预测更频繁。</p></div>
        <div><CalibrationChart bins={calibration[model][side]} side={side}/></div></div>
    </section>}
    <section className="panel"><header className="section-head"><h3>逐季历史成绩</h3><span>{h.n.toLocaleString()} 场回放</span></header>
      {d&&<div className="rp-toolbar"><label>逐季指标<select value={metric} onChange={e=>setMetric(e.target.value as typeof metric)}><option value="log_loss">对数损失 ↓</option><option value="brier">Brier ↓</option><option value="accuracy">命中率</option></select></label></div>}
      <div className="table-wrap"><table><thead><tr><th>赛季</th><th>比赛</th>{d?components.map(k=><th key={k}>{modelNames[k]}</th>):<><th>A 对数损失</th><th>Elo 对数损失</th></>}</tr></thead>
      <tbody>{d?[...d.seasons].reverse().filter(s=>!season||season===s.season).map(s=><tr key={s.season}><th>{s.season}</th><td>{s.n}</td>{components.map(k=><td key={k}>{metric==='accuracy'?pct(s.scores[k][metric]):num(s.scores[k][metric],4)}</td>)}</tr>):
        [...h.seasons].reverse().filter(s=>!season||season===s.season).map(s=><tr key={s.season}><th>{s.season}</th><td>{s.n}</td><td>{num(s.log_loss,4)}</td><td>{num(s.elo_loss,4)}</td></tr>)}</tbody></table></div></section>
  </>
}

type Row = {id:string;season:string;date:string;home:string;away:string;actual:Outcome|null;cutoff?:string|null;
  reason:string;version?:string; evidence?:string|null;probs:Partial<Record<Component|'market'|'b',Triple|null>>}
function historyRow(r:HistoryRow):Row {
  return {id:r.id,season:r.season,date:r.date,home:r.home,away:r.away,actual:r.actual,cutoff:r.cutoff,
    reason:components.some(k=>r.probs[k]==null)?'missing_component':'complete',probs:r.probs,version:r.model_version}
}
function marketRow(r:MarketRow&{model_version?:string}):Row {
  const actual=typeof r.actual==='number'?outcomes[r.actual]:r.actual
  return {id:r.id,season:r.season,date:r.kickoff||'',home:r.home||'',away:r.away||'',actual:outcomes.includes(actual as Outcome)?actual as Outcome:null,
    reason:r.reason||r.status||'no_snapshot',cutoff:r.cutoff,version:r.model_version,evidence:r.evidence,probs:{a:r.a,market:r.market,b:r.b}}
}
function actualProbability(r:Row,k:Component|'b') {
  return r.actual?r.probs[k]?.[outcomes.indexOf(r.actual)]??null:null
}
function marketGap(r:Row) {
  const m=r.probs.market,b=r.probs.b
  return m && b?Math.max(...m.map((v,i)=>Math.abs(v-b[i]))):null
}
function downloadRows(rows:Row[], cohort:Cohort, season:string) {
  const keys: (Component|'market'|'b')[] = cohort==='history'?components:['a','market','b']
  const heading=['cohort','fixture_id','season','date','home','away','actual','cutoff','model_version','status_reason','evidence',...keys.flatMap(k=>outcomes.map(o=>k+'_'+o))]
  const cell=(v:unknown)=>{
    const s=String(v??'')
    return '"'+(/^[=+@\-\t\r]/.test(s)?"'"+s:s).replace(/"/g,'""')+'"'
  }
  const lines=rows.map(r=>[cohort,r.id,r.season,r.date,r.home,r.away,r.actual,r.cutoff,r.version,r.reason,r.evidence,...keys.flatMap(k=>r.probs[k]||[null,null,null])])
  const url=URL.createObjectURL(new Blob(['\uFEFF'+[heading,...lines].map(row=>row.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}))
  const a=document.createElement('a')
  a.href=url; a.download='epl-'+cohort+'-'+(season||'all-seasons')+'.csv'; document.body.append(a); a.click(); a.remove()
  window.setTimeout(()=>URL.revokeObjectURL(url),1000)
}
async function fetchHistory(season:string,page:number|null,partitioned:boolean,signal?:AbortSignal):Promise<Row[]> {
  const params=new URLSearchParams()
  if(season&&partitioned)params.set('season',season)
  if(page!==null&&partitioned)params.set('page',String(page+1))
  const year=encodeURIComponent(season||'all')
  const url=isStatic()?(partitioned&&page!==null?'./history/'+year+'/'+(page+1)+'.json':
    season&&partitioned?'./history/'+year+'.json':'./history-rows.json'):'/api/history-rows?'+params
  const response=await fetch(url,{signal})
  if(!response.ok)throw new Error('Rows unavailable')
  const rows:unknown=await response.json()
  if(!Array.isArray(rows))throw new Error('Unexpected response')
  return (rows as HistoryRow[]).map(historyRow)
}

function MatchRecords({r,openMatch,partitioned}:{r:Scores;openMatch:OpenMatch;partitioned:boolean}) {
  const [cohort,setCohort]=useState<Cohort>('history'), [season,setSeason]=useState(''), [reason,setReason]=useState('')
  const [sort,setSort]=useState('date'), [page,setPage]=useState(0), [retry,setRetry]=useState(0)
  const paged=partitioned&&cohort==='history'&&sort==='date'&&!reason
  const pageKey=paged?page:null
  const [history,setHistory]=useState<{key:string;rows:Row[]}|null>(null), [error,setError]=useState(false)
  const [downloading,setDownloading]=useState(false),[downloadError,setDownloadError]=useState(false)
  const queryKey=JSON.stringify([season,pageKey])
  useEffect(()=>{
    if(cohort!=='history')return
    const controller=new AbortController()
    setError(false)
    fetchHistory(season,pageKey,partitioned,controller.signal).then(rows=>{if(!controller.signal.aborted)setHistory({key:queryKey,rows})})
      .catch(()=>{if(!controller.signal.aborted)setError(true)})
    return ()=>controller.abort()
  },[cohort,queryKey,pageKey,season,partitioned,retry])
  const pending=cohort==='history'&&history?.key!==queryKey&&!error
  const rows=useMemo(()=>cohort==='history'?(history?.key===queryKey?history.rows:[]):(cohort==='independent'?
    [...(r.pilot?.independent?.rows||[]),...(r.independent?.rows||[])]:((cohort==='pilot'?r.pilot?.rows:r.rows)||[])).map(marketRow),[cohort,history,r,queryKey])
  const seasons=useMemo(()=>Array.from(new Set(cohort==='history'?r.history.seasons.map(s=>s.season):rows.map(m=>m.season))).sort().reverse(),[rows,cohort,r])
  const seasonRows=useMemo(()=>rows.filter(m=>!season||m.season===season),[rows,season])
  const codes=useMemo(()=>paged?['complete','missing_component']:Array.from(new Set([...seasonRows.map(m=>m.reason),...(reason?[reason]:[])])).sort(),[seasonRows,paged,reason])
  const component=cohort==='history'||cohort==='independent'?'a':'b'
  const filtered=useMemo(()=>{
    let list=seasonRows.filter(m=>!reason||reason===m.reason)
    if(sort==='worst') {
      const ranked=list.flatMap(row=>{const p=actualProbability(row,component);return p===null?[]:[{row,p}]})
      list=ranked.sort((a,b)=>a.p-b.p||a.row.id.localeCompare(b.row.id)).slice(0,10).map(x=>x.row)
    }else if(sort==='gap') {
      const ranked=list.flatMap(row=>{const gap=marketGap(row);return gap===null?[]:[{row,gap}]})
      list=ranked.sort((a,b)=>b.gap-a.gap||a.row.id.localeCompare(b.row.id)).slice(0,10).map(x=>x.row)
    }else list.sort((a,b)=>b.date.localeCompare(a.date)||a.id.localeCompare(b.id))
    return list
  },[seasonRows,reason,sort,component])
  const pageSize=20
  const total=paged?(season?r.history.seasons.find(s=>s.season===season)?.n||0:r.history.n):filtered.length
  const lastPage=Math.max(0,Math.ceil(total/pageSize)-1), current=Math.min(page,lastPage)
  async function download(){
    setDownloading(true);setDownloadError(false)
    try{
      const all=paged?(await fetchHistory(season,null,partitioned)).sort((a,b)=>b.date.localeCompare(a.date)||a.id.localeCompare(b.id)):filtered
      downloadRows(all,cohort,season)
    }catch{setDownloadError(true)}finally{setDownloading(false)}
  }
  return <section className="panel"><header className="section-head"><h3>逐场记录</h3>
    <button className="rp-download" disabled={downloading||!filtered.length} onClick={()=>void download()}>{downloading?'正在准备 CSV…':'下载当前筛选 CSV ↓'}</button></header>
    {downloadError&&<p role="alert">下载失败，请重试。</p>}
    <div className="rp-toolbar">
      <label>记录来源<select value={cohort} onChange={e=>{setCohort(e.target.value as Cohort);setSeason('');setReason('');setSort('date');setPage(0)}}>{(Object.keys(cohortNames) as Cohort[]).map(k=><option key={k} value={k}>{cohortNames[k]}</option>)}</select></label>
      <label>记录赛季<select value={season} onChange={e=>{setSeason(e.target.value);setReason('');setPage(0)}}><option value="">全部赛季</option>{seasons.map(s=><option key={s}>{s}</option>)}</select></label>
      <label>缺失原因或状态<select value={reason} onChange={e=>{setReason(e.target.value);setPage(0)}}><option value="">全部状态</option>{codes.map(k=><option key={k} value={k}>{reasonName(k)}</option>)}</select></label>
      <label>场次筛选<select value={sort} onChange={e=>{setSort(e.target.value);setPage(0)}}><option value="date">全部 · 按日期</option><option value="worst">预测偏差最大 10 场</option>{component==='b'&&<option value="gap">市场分歧最大 10 场</option>}</select></label>
    </div>
    {pending?<p className="muted" role="status">正在读取历史逐场概率…</p>:cohort==='history'&&error?
      <p className="muted" role="alert">历史明细加载失败。 <button className="plain" onClick={()=>setRetry(n=>n+1)}>重试</button></p>:<>
      <p className="rp-summary" role="status">{total.toLocaleString()} 场{sort==='worst'?' · 按模型给真实赛果的概率从低到高排列':sort==='gap'?' · 按模型 B 与市场的最大绝对概率差排列':''}</p>
      {filtered.length>0?<><div className="table-wrap" tabIndex={0} aria-label="逐场预测列表"><table className="rp-record-table"><thead><tr>
        <th>比赛日期</th><th>对阵</th><th>实际赛果</th><th>{component.toUpperCase()} 对赛果的概率</th>{component==='b'&&<th>市场对赛果的概率</th>}<th>状态</th></tr></thead>
        <tbody>{(paged?filtered:filtered.slice(current*pageSize,(current+1)*pageSize)).map((m,i)=><tr key={m.id+'-'+i}>
          <td>{m.date?fmtTime(m.date,true):'待核实'}</td><th><button className="rp-match" onClick={()=>openMatch(m.id,m.season)}>{m.home} <span>vs</span> {m.away}</button></th>
          <td>{m.actual?sides[m.actual]:'—'}</td><td>{pct(actualProbability(m,component))}</td>
          {component==='b'&&<td>{pct(m.actual?m.probs.market?.[outcomes.indexOf(m.actual)]:null)}</td>}
          <td>{m.evidence&&/^https:\/\/github\.com\//.test(m.evidence)?<a className="rp-link" href={m.evidence} target="_blank" rel="noopener">{reasonName(m.reason)} ↗</a>:reasonName(m.reason)}</td>
        </tr>)}</tbody></table></div>
        {lastPage>0&&<nav className="rp-pages" aria-label="逐场记录翻页"><button disabled={!current} onClick={()=>setPage(current-1)}>上一页</button>
        <span>{current+1} / {lastPage+1}</span><button disabled={current===lastPage} onClick={()=>setPage(current+1)}>下一页</button></nav>}</>:
        <p className="muted">当前筛选没有可显示的记录。</p>}
    </>}
  </section>
}

export function Report({r,openMatch,publicSnapshot=false,partitioned=false,fixedSnapshot}:{r:Scores;openMatch:OpenMatch;publicSnapshot?:boolean;partitioned?:boolean;fixedSnapshot?:string}) {
  return <div className="report"><MarketReport r={r}/><PilotReceipt r={r} openMatch={openMatch} publicSnapshot={publicSnapshot} fixedSnapshot={fixedSnapshot}/>
    <HistorySummary r={r}/><MatchRecords r={r} openMatch={openMatch} partitioned={partitioned}/></div>
}
