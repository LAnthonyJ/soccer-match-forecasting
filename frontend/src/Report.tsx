import { type Report as Scores, statusLabels, num, pct, isStatic } from './data'

const missingLabels:Record<string,string>={no_odds:'缺少合格赔率',no_snapshot:'缺少赛前快照',rescheduled:'赛程变更',missing_market:'缺少市场基准',missing_prediction:'缺少模型预测',missed_window:'错过采集窗口',insufficient_books:'有效书商不足'}
export function Report({r,openMatch}:{r:Scores;openMatch:(id:string,season:string)=>void}) {
  const h=r.history
  return <div className="report">
    <section className="report-banner"><div><small>T−24h · 市场对照</small><h2>{statusLabels[r.status]}</h2><p>同一场比赛、同一赛前时点，比较模型与去水市场概率。</p></div>
    <div className="report-stat"><b>{r.n_pairs.toLocaleString()}</b><small>有效配对场次</small></div></section>
    <div className="report-grid"><section className="panel"><h3>前瞻评测</h3>
      <dl className="metrics"><div><dt>对数损失改善量</dt><dd>{num(r.delta,4)}</dd></div><div><dt>95% 置信区间</dt><dd>{r.ci?r.ci.map(v=>num(v,4)).join(' 至 '):'样本不足'}</dd></div></dl>
      <div className="table-wrap"><table><thead><tr><th>赛季</th><th>赛程</th><th>配对</th><th>覆盖率</th><th>模型 B 损失</th><th>市场损失</th></tr></thead><tbody>{r.seasons.map(s=><tr key={s.season}><th>{s.season}</th><td>{s.total}</td><td>{s.paired}</td><td>{pct(s.coverage)}</td><td>{num(s.model_loss)}</td><td>{num(s.market_loss)}</td></tr>)}</tbody></table></div>
      {!r.seasons.length&&<p className="muted">尚无符合协议的赛季成绩。</p>}
      <a className="ledger" href={isStatic()?'./ledger.json':'/api/ledger'} target="_blank" rel="noopener">查看赛前预测账本（JSON）↗</a>
      <details className="protocol"><summary>评测门槛与缺失场次</summary><p>冻结后的两个完整赛季，各季覆盖率至少 90%。市场减模型的平均对数损失至少为 0.005，比赛周分块重采样的 95% 区间下界须大于零。</p>
      {Object.entries(r.missing||{}).map(([k,n])=><div key={k}>{missingLabels[k]||k} · {n} 场</div>)}</details>
    </section><section className="panel"><header className="section-head"><h3>模型 A 校准</h3><span>历史回放</span></header>
      <p className="muted">比较预报概率与对应结果的实际出现比例。</p>
      {h.calibration.length ? <div className="calibration"><div className="cal-head"><span>预报</span><span>实际频率</span><span>样本</span></div>{h.calibration.map((b,i)=><div className="cal-row" key={i}><span>{pct(b.p)}</span><div className="cal-bar"><i style={{width:pct(b.observed)}}/><b>{pct(b.observed)}</b></div><span>{b.n}</span></div>)}</div>:<p className="notice">暂无已评分的历史样本。</p>}
    </section></div>
    <section className="panel"><header className="section-head"><h3>逐季历史成绩</h3><span>{h.n.toLocaleString()} 场回放</span></header>
      <div className="table-wrap"><table><thead><tr><th>赛季</th><th>比赛</th><th>A 对数损失 ↓</th><th>Elo 对数损失 ↓</th><th>Brier ↓</th><th>命中率</th></tr></thead><tbody>{[...h.seasons].reverse().map(s=><tr key={s.season}><th>{s.season}</th><td>{s.n}</td><td>{num(s.log_loss)}</td><td>{num(s.elo_loss)}</td><td>{num(s.brier)}</td><td>{pct(s.accuracy)}</td></tr>)}</tbody></table></div>
      <p className="muted">历史成绩衡量独立模型；市场对照另用赛前冻结记录计算。</p>
    </section>
    <section className="panel"><header className="section-head"><h3>预测偏差最大的比赛</h3><span>按真实结果的预测概率排序</span></header>
    {!h.worst.length&&<p className="muted">尚无记录。</p>}<div className="worst-list">{h.worst.slice(0,10).map(m=><button key={m.id} onClick={()=>openMatch(m.id,m.season)}>
      <span>{m.season}</span><b>{m.home} <i>vs</i> {m.away}</b><strong>{({H:'主胜',D:'平局',A:'客胜'} as Record<string,string>)[String(m.actual)]||''} {pct(m.prob)}</strong><span aria-hidden="true">↗</span></button>)}</div></section>
  </div>
}
