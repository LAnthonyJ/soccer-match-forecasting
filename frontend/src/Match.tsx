import { useState } from 'react'
import { type Fixture, type Prediction, type Triple, fixtureStatus, nameOf, fmtTime, num, pct, sides, difference, reasons } from './data'

const clubColours: [RegExp,string,string][] = [
  [/arsenal/i,'#ca3741','ARS'],[/manchester city|man city/i,'#71add3','MCI'],[/liverpool/i,'#b93947','LIV'],
  [/manchester united|man utd/i,'#cf4e46','MUN'],[/chelsea/i,'#4469b0','CHE'],[/tottenham/i,'#8796b9','TOT'],
  [/aston villa/i,'#964c69','AVL'],[/newcastle/i,'#5d6872','NEW'],[/brighton/i,'#417bb6','BHA'],
  [/bournemouth/i,'#b35149','BOU'],[/brentford/i,'#c35655','BRE'],[/crystal palace/i,'#565eb7','CRY'],
  [/everton/i,'#396cbe','EVE'],[/fulham/i,'#75818c','FUL'],[/west ham/i,'#9d546f','WHU'],
  [/wolv/i,'#a27419','WOL'],[/nottingham/i,'#b8444c','NFO'],[/leeds/i,'#8c7733','LEE'],
  [/leicester/i,'#4273b5','LEI'],[/sunderland/i,'#b65257','SUN'],[/burnley/i,'#944a60','BUR'],
]
export function club(team:string):[string,string] {
  const found=clubColours.find(([re])=>re.test(team))
  if(found) return [found[1],found[2]]
  return ['#437c73',team.replace(/[^A-Za-z]/g,'').slice(0,3).toUpperCase() || 'FC']
}
export function TeamMark({team,small=false}:{team:string;small?:boolean}) {
  const [colour,code]=club(team)
  return <span aria-hidden="true" className={'team-mark'+(small?' mini':'')} style={{'--team':colour} as React.CSSProperties}>{small?'':code}</span>
}
function ModelChart({title,probs,kind}:{title:string;probs:Triple|null;kind:string}) {
  return <div className={'chart-row '+kind}><div className="chart-name">{title}</div>{sides.map((s,i)=>
    <div key={s} className="chart-cell"><span>{pct(probs?.[i])}</span><div className="track"><i style={{width:(probs?probs[i]*100:0)+'%'}}/></div></div>)}</div>
}
export function Match({f,openAsk}:{f:Fixture;openAsk:(q:string)=>void}) {
  const [reveal,setReveal]=useState(false)
  const p=f.prediction, gap=p?difference(p):null
  return <article className="match-detail" aria-label="比赛详情">
    <div className="scoreboard">
      <div className="pitch-art" aria-hidden="true"><div/></div>
      <div className="score-meta"><span><i className="signal"/> {fixtureStatus(f)}</span><span>{f.season.replace('-',' / ')} · 英超</span></div>
      <h2 className="matchup">
        <span className="side"><TeamMark team={f.home}/><span>{nameOf(f,'home')}</span><small>主队</small></span>
        <span className="versus">vs</span>
        <span className="side"><TeamMark team={f.away}/><span>{nameOf(f,'away')}</span><small>客队</small></span>
      </h2>
      <div className="kickoff">{f.kickoff_utc?fmtTime(f.kickoff_utc)+' 开球':fmtTime(f.date,true)+' · 开球时刻待确认'}</div>
    </div>
    {p ? <>
      <section className="panel chart-panel"><header className="section-head"><h3>胜平负预测</h3><span>常规时间含补时</span></header>
        <div className="outcomes">{p.a.map((v,i)=><div key={i}><div className={'outcome-label tone-'+i}><i/>{sides[i]}</div><b>{(100*v).toFixed(1)}<small>%</small></b></div>)}</div>
        <div className="stack" aria-label="模型 A 概率分布">{p.a.map((v,i)=><i key={i} className={'tone-'+i} style={{width:(v*100)+'%'}}/>)}</div>
        <div className="chart-caption">独立模型 A · 足球数据</div>
        <div className="comparison"><div className="chart-row colnames"><span>概率对照</span>{sides.map(s=><span key={s}>{s}</span>)}</div>
          <ModelChart title="独立模型 A" probs={p.a} kind="model-a"/>
          <ModelChart title="市场基准" probs={p.market} kind="market"/>
          <ModelChart title="融合模型 B" probs={p.b} kind="model-b"/>
        </div>
        {gap ? <p className="notice">{gap.model}与市场的最大分歧为{gap.side}，相差 {(Math.abs(gap.gap)*100).toFixed(1)} 个百分点。</p> :
          <details className="notice market-missing"><summary>缺少市场报价</summary><p>{f.market_note||'缺少同场、同时间的合格市场报价。'}</p></details>}
      </section>
      <section className="panel"><header className="section-head"><h3>比分与进球</h3><span>独立模型 A</span></header>
        <div className="scorelines">{p.top_scores.slice(0,3).map(([h,a,v])=><div key={h+'-'+a}><strong>{h} <i>:</i> {a}</strong><span>{pct(v)}</span></div>)}</div>
        <div className="xg"><span>预期进球</span><b>{num(p.xg[0],2)} <small>主队</small>　/　{num(p.xg[1],2)} <small>客队</small></b></div>
      </section>
      {p.form?.home&&p.form.away&&<section className="panel"><header className="section-head"><h3>近期表现</h3><span>最近最多 8 场 · 4 场先验平滑</span></header>
        <div className="table-wrap"><table><thead><tr><th>球队</th><th>进球</th><th>失球</th><th>积分</th></tr></thead><tbody>
        {(['home','away'] as const).map(s=><tr key={s}><th>{nameOf(f,s)}</th><td>{num(p.form![s].gf,2)}</td><td>{num(p.form![s].ga,2)}</td><td>{num(p.form![s].points,2)}</td></tr>)}
        </tbody></table></div></section>}
      <details className="panel provenance" open><summary>预测依据</summary><dl>
        <div><dt>数据截止</dt><dd>{fmtTime(p.cutoff)}</dd></div><div><dt>模型版本</dt><dd>{p.model_version}</dd></div>
        <div><dt>累计英超样本 · 主 / 客</dt><dd>{p.samples?.join(' / ') || '暂无'}</dd></div>
        <div><dt>预测来源</dt><dd>{p.source==='preview'?'按当前资料预览':p.source==='frozen'?'赛前冻结快照':'按历史时间重建'}</dd></div>
      </dl>{f.explanation&&<p className="muted explanation">{f.explanation}</p>}
      {f.evidence&&<a className="ledger" href={f.evidence} target="_blank" rel="noopener">核对赛前公开见证 ↗</a>}</details>
      <button className="ask-strip" onClick={()=>openAsk('这场比赛模型为什么给出这样的概率？')}>询问这场比赛的预测依据 <span aria-hidden="true">↗</span></button>
    </>:<section className="panel"><h3>暂无合格预测</h3><p className="muted">{f.freeze_note||'这场比赛的赛程或赛前资料尚未满足预测条件。'}</p>
      {f.market_note&&f.market_note!==f.freeze_note&&<details className="notice market-missing"><summary>缺少市场报价</summary><p>{f.market_note}</p></details>}</section>}
    {f.trace&&<details className="panel provenance"><summary>比赛时间线 · 赛程版本 {f.trace.revision}</summary>
      <dl>{([['赛程确认',f.trace.schedule_at],['计划截止',f.trace.cutoff],['实际冻结',f.trace.frozen],
        ['公开见证',f.trace.witnessed],['收到确认赛果',f.trace.result_at],['本批次结算截至',f.trace.score_as_of],
        ['首次核实本版记录已上线',f.trace.deployed?.seen_at]] as const).map(([label,value])=>
          <div key={label}><dt>{label}</dt><dd>{value?fmtTime(value):'暂无记录'}</dd></div>)}</dl>
      {f.trace.freeze_reason&&<p>{f.freeze_note||reasons[f.trace.freeze_reason]||f.trace.freeze_reason}</p>}
      {f.trace.witness_status==='late'&&<p className="notice">见证晚于截止时间，本场未计入前瞻成绩。</p>}
      {f.trace.schedule_history.length>1&&<ul>{f.trace.schedule_history.map(v=><li key={v.revision}>
        版本 {v.revision} · 开球 {fmtTime(v.kickoff)} {v.withdrawn?'已撤回':''} {v.evidence&&<a href={v.evidence}>查看原见证</a>}</li>)}</ul>}
      {f.trace.score&&<p>模型 A 本场对数损失 <b>{num(f.trace.score.log_loss,4)}</b>，Brier <b>{num(f.trace.score.brier,4)}</b>。</p>}
      {f.trace.hash&&<p className="trace-hash">赛前 SHA-256 · <code>{f.trace.hash}</code></p>}
    </details>}
    {Boolean(f.source_conflict)&&<p className="notice">两个数据源对赛果的记录不同，本场暂停结算。</p>}
    {f.hg!=null&&f.ag!=null&&<section className="result"><span>赛后结果</span><button aria-expanded={reveal} onClick={()=>setReveal(!reveal)}>{reveal?'隐藏赛果':'查看真实赛果'}</button>
      {reveal&&<strong>{nameOf(f,'home')} {f.hg} – {f.ag} {nameOf(f,'away')}</strong>}</section>}
  </article>
}
export function ListChance({p}:{p:Prediction|null}) {
  if (!p) return <span className="list-empty">暂无预测</span>
  return <span className="list-chance">{p.a.map((v,i)=><span key={i} className={'tone-'+i}><i/>{['主','平','客'][i]} {Math.round(v*100)}%</span>)}</span>
}
