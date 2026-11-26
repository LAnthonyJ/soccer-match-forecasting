import { AnalysisPanel } from './Analysis'
import { TeamMark } from './TeamMark'
import { type Fixture, type Triple, nameOf, fmtTime, pct, sides, difference, weekOf } from './data'
import { fanPhase, forecastLine, localZoneLabel } from './fanFixture'
import './Match.css'

function ModelChart({title,probs}:{title:string;probs:Triple}) {
  return <tr><th scope="row">{title}</th>{probs.map((v,i)=><td key={i}>{pct(v)}</td>)}</tr>
}

function Market({f}:{f:Fixture}) {
  const p=f.prediction
  if(!p?.market) return <div className="fan-market-note">暂无同一时点的合格市场报价</div>
  const gap=difference(p)
  return <details className="fan-market"><summary>市场怎么看</summary>
    <table><thead><tr><th scope="col">概率对照</th>{sides.map(side=><th scope="col" key={side}>{side}</th>)}</tr></thead>
      <tbody><ModelChart title="足球模型" probs={p.a}/><ModelChart title="市场" probs={p.market}/>
        {p.b&&<ModelChart title="融合模型" probs={p.b}/>}</tbody></table>
    {gap&&<p>{gap.model}与市场的最大分歧在{gap.side}，相差 {(Math.abs(gap.gap)*100).toFixed(1)} 个百分点。</p>}
  </details>
}

function Highlights({f}:{f:Fixture}) {
  const p=f.prediction
  if(!p) return null
  const home=nameOf(f,'home'),away=nameOf(f,'away')
  const form=p.form
  const recent=form?.home && form.away && p.samples?.every(n=>n>0)
  const goals=p.xg?.every(Number.isFinite)
  return <section className="fan-insights" aria-label="赛前看点"><h3>赛前看点</h3>
    {recent&&<div className="fan-insight"><h4>近期进攻</h4><p>近期场均进球，{home} <b>{form.home.gf.toFixed(2)}</b> 球，{away} <b>{form.away.gf.toFixed(2)}</b> 球。</p>
      <small>取近 {Math.min(8,p.samples[0])} / {Math.min(8,p.samples[1])} 场联赛，加入 4 场联赛平均水平平滑估计。</small></div>}
    {goals&&<div className="fan-insight"><h4>模型预计进球</h4><p>根据本场比分分布，预计{home}进 <b>{p.xg[0].toFixed(2)}</b> 球，{away}进 <b>{p.xg[1].toFixed(2)}</b> 球。</p></div>}
    {!recent&&!goals&&<p>暂缺可核对的球队近况。</p>}
  </section>
}

export function Match({f}:{f:Fixture;openAsk?:(q:string)=>void}) {
  const phase=fanPhase(f),p=f.prediction
  const home=nameOf(f,'home'), away=nameOf(f,'away')
  const final=phase==='finished'
  const phaseLabel={scheduled:'未开赛',waiting:'等待赛果',finished:'已完场',conflict:'比分待核实',unscheduled:'开球时间待定'}[phase]
  const time=phase==='unscheduled'||!f.kickoff_utc?fmtTime(f.date,true):fmtTime(f.kickoff_utc)+' · '+localZoneLabel()
  const outcomes=[home+'胜','平局',away+'胜']
  return <article className="match-detail fan-match" aria-label="比赛详情">
    <header className="fan-matchhead"><div className="fan-field" aria-hidden="true"><i/><i/></div>
      <div className="fan-headline"><span>英超 · {weekOf(f)}</span><strong>{phaseLabel}</strong></div>
      <h2 className="fan-versus"><span className="fan-hero-team"><small>主队</small><strong>{home}</strong><TeamMark team={f.home}/></span>
        {final?<span className="fan-score" aria-label={'比分 '+f.hg+' 比 '+f.ag}>{f.hg}–{f.ag}</span>:<span className="fan-vs">VS</span>}
        <span className="fan-hero-team"><small>客队</small><strong>{away}</strong><TeamMark team={f.away}/></span></h2>
      <p className="fan-kickoff">{time}{phase==='scheduled'?' 开球':''}</p>
    </header>
    {phase==='scheduled' && p && <>
      <section className="fan-outlook" aria-label="本场赛前预测"><header><h3>胜平负预测</h3><span>常规时间含补时</span></header>
        <div className="fan-odds">{p.a.map((v,i)=><div className={'fan-odd tone-'+i} key={i}><span><i/>{outcomes[i]}</span>
          <strong>{(100*v).toFixed(1)}<small>%</small></strong></div>)}</div>
        <div className="fan-probbar" aria-hidden="true">{p.a.map((v,i)=><i key={i} className={'tone-'+i} style={{flexGrow:v}}/>)}</div>
        <p className="fan-judgment">{forecastLine(f)}</p>
        <p className="fan-stamp">{p.source==='preview'?'赛前预览，仍可能更新':p.source==='frozen'?'赛前已冻结':'按历史时点重建'} · {fmtTime(p.cutoff)} {localZoneLabel()}</p>
      </section>
      <Highlights f={f}/>
      <AnalysisPanel key={f.id} f={f}/>
      <Market f={f}/>
    </>}
    {phase==='scheduled'&&!p&&<p className="fan-match-note">{f.freeze_note||'这场比赛暂时没有可用的赛前预测。'}</p>}
    {phase==='unscheduled'&&<p className="fan-match-note">开球时刻经核实后，这里将显示可用的赛前预测。</p>}
    {phase==='waiting'&&<p className="fan-match-note">已过开球时间，正在等待确认比分。</p>}
    {phase==='conflict'&&<p className="fan-match-note">两家数据来源的记录不同，待核实后显示终场比分。</p>}
    <div className="fan-sources"><a href={'./about.html?fixture='+encodeURIComponent(f.id)}>本场数据来源与方法 ↗</a></div>
  </article>
}
