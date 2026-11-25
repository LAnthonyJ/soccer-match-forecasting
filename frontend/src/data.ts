export type Triple = [number, number, number]
export interface Prediction {
  a: Triple; b: Triple | null; market: Triple | null; xg: [number, number];
  top_scores: [number, number, number][]; cutoff: string; model_version: string;
  samples: [number, number]; form?: Record<'home'|'away',{gf:number;ga:number;points:number}>; source: 'replay' | 'frozen' | 'preview';
}
export interface Fixture {
  id: string; season: string; date: string; kickoff_utc: string | null;
  home: string; away: string; home_zh?: string; away_zh?: string;
  round?: string | number | null; hg: number | null; ag: number | null; prediction: Prediction | null;
}
export interface Report {
  status: 'accumulating' | 'unproven' | 'leading'; n_pairs: number;
  seasons: { season: string; total: number; paired: number; coverage: number; model_loss: number|null; market_loss: number|null }[];
  delta: number|null; ci: [number,number]|null; missing: Record<string,number>;
  history: { n:number; seasons: {season:string; n:number; log_loss:number; brier:number; accuracy:number; elo_loss:number}[];
    calibration: {p:number; observed:number; n:number}[];
    worst: {id:string; home:string; away:string; season:string; prob:number; actual:string|number}[] };
}
export interface Dashboard {
  fixtures: Fixture[]; default_id:string; updated_at:string; source_updated_at?:string; data_version?:string; available_seasons?:string[]; report:Report;
}
export type Choice = {id:string; label:string}
export type Answer = {answer:string;choices?:Choice[]}
export const pct = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : (value*100).toFixed(1)+'%'
export const num = (n: number|null|undefined, d=3) => n == null || !Number.isFinite(n) ? '—' : n.toFixed(d)
export const sides = ['主胜','平局','客胜']
export const labels = {replay:'历史回放',frozen:'赛前已冻结',preview:'预测预览'}
export const statusLabels = {accumulating:'积累样本中',unproven:'尚未证实领先',leading:'在所列赛季和时点领先'}
export const nameOf = (f: Fixture, s: 'home' | 'away') => f[s+'_zh' as 'home_zh'|'away_zh'] || f[s]
export function fixtureStatus(f: Fixture) {
  if(f.prediction) return labels[f.prediction.source]
  if(f.hg!=null && f.ag!=null) return '已收录赛果'
  const expectedEnd=Date.parse(f.kickoff_utc||f.date+'T23:59:59Z')+3*3600000
  return expectedEnd<Date.now()?'赛果待更新':'收录赛程'
}
export function fmtTime(iso: string | null | undefined, dateOnly=false) {
  if (!iso) return '待确认'
  const day = /^\d{4}-\d{2}-\d{2}$/.test(iso)
  const when = new Date(day ? iso+'T12:00:00Z' : iso)
  if (Number.isNaN(+when)) return '待确认'
  return new Intl.DateTimeFormat('zh-CN', {year:'numeric',month:'2-digit',day:'2-digit',
    ...(day ? {timeZone:'UTC'} : dateOnly ? {} : {hour:'2-digit',minute:'2-digit',hour12:false})}).format(when)
}
export function weekOf(f: Fixture) {
  if (f.round != null && String(f.round).length) {
    const text = String(f.round)
    const n = text.match(/^(?:(?:round|matchday|week)\s*)?(\d+)$/i)
    return n ? '第 '+n[1]+' 轮' : text
  }
  const d = new Date(f.date+'T12:00:00Z')
  if (!Number.isFinite(+d)) return '日期待确认'
  d.setUTCDate(d.getUTCDate()-(d.getUTCDay()+6)%7)
  return d.toISOString().slice(0,10)+' 周'
}
export async function fetchDash(season?:string, signal?:AbortSignal): Promise<Dashboard> {
  const response = await fetch(isStatic() ? './dashboard.json' : '/api/dashboard'+(season?'?season='+encodeURIComponent(season):''), {signal})
  if (!response.ok) throw new Error('无法加载比赛数据')
  const result = await response.json()
  if (!Array.isArray(result.fixtures) || !result.report?.history) throw new Error('比赛数据格式不完整')
  return result
}
export function isStatic() { return document.querySelector('meta[name="data-mode"]')?.getAttribute('content') === 'static' }
export function difference(p:Prediction) {
  if (!p.market) return null
  const model = p.b || p.a
  const gaps = model.map((v,i)=>v-p.market![i])
  const i = gaps.reduce((a,b,i)=>Math.abs(b)>Math.abs(gaps[a])?i:a,0)
  return {side:sides[i],gap:gaps[i],model:p.b?'融合模型 B':'独立模型 A'}
}
export function localAnswer(question:string,selected: Fixture|undefined, data:Dashboard):Answer {
  const q = question.trim().toLowerCase()
  if (/伤|停赛|首发|阵容/.test(q)) return {answer:'缺少可核实的实时伤停和首发数据。当前模型使用已结束比赛的结果和赛程信息。'}
  if (/成绩|过去一季|战绩|领先市场|击败市场/.test(q)) {
    const r = data.report
    const last = [...r.seasons].sort((a,b)=>b.season.localeCompare(a.season))[0]
    return {answer:'正式评测状态为「'+statusLabels[r.status]+'」，累计 '+r.n_pairs+' 场有效配对。'+(last?'最近评测赛季 '+last.season+'，覆盖率 '+pct(last.coverage)+'。':'尚无已评分的前瞻赛季。') }
  }
  const clubs=new Set<string>()
  for (const row of data.fixtures) for (const side of ['home','away'] as const) {
    if ([row[side],nameOf(row,side)].some(n=>n && q.includes(n.toLowerCase()))) clubs.add(row[side])
  }
  let f=selected
  if (clubs.size || /周末|下一场|下场/.test(q)) {
    let games=data.fixtures.filter(m=>[...clubs].every(t=>m.home===t||m.away===t))
    if (/周末|下场|下一场/.test(q)) {
      const now=new Date(), start=new Date(now)
      start.setHours(0,0,0,0)
      const sat=new Date(start)
      sat.setDate(start.getDate()+((6-start.getDay()+7)%7)-(start.getDay()===0?7:0))
      const end=new Date(sat); end.setDate(sat.getDate()+2)
      games=games.filter(m=>{const d=new Date(m.kickoff_utc || m.date+'T12:00:00');return /周末/.test(q)?d>=sat&&d<end:d>=now})
    }
    games.sort((a,b)=>a.date.localeCompare(b.date))
    if (!games.length) return {answer:'该时间范围的演示赛程中没有匹配的比赛。请在比赛看板选择已收录的场次。'}
    if(games.length>1 && !/下场|下一场/.test(q)) return {answer:'找到多个匹配场次，请选择一场。',choices:games.slice(-8).reverse().map(m=>({id:m.id,label:m.date+' · '+nameOf(m,'home')+' vs '+nameOf(m,'away')}))}
    f=games[0]
  }
  if(!f) return {answer:'请先选择要查看的比赛。'}
  const title=nameOf(f,'home')+' vs '+nameOf(f,'away')+'（'+f.date+'）'
  if (/周末|对手|下一场|下场|踢谁/.test(q)) return {answer:title+'，开球时间 '+fmtTime(f.kickoff_utc)+'。'}
  const p=f.prediction
  if(!p) return {answer:title+' 还没有合格的赛前预测。'}
  if(/市场|赔率|分歧/.test(q)) {
    const d=difference(p)
    return {answer:title+'。'+(d?d.model+'与市场在'+d.side+'上的分歧最大，概率相差 '+(100*Math.abs(d.gap)).toFixed(1)+' 个百分点。':'此快照缺少合格的 T−24h 市场数据，暂时无法比较。')}
  }
  if (!/概率|看好|赢|预测|平局|主胜|客胜|为什么/.test(q)) return {answer:'可以询问赛程、胜平负概率、市场分歧或模型成绩。'}
  return {answer:title+'。独立模型 A 的主胜、平局、客胜概率分别为 '+p.a.map(pct).join('、')+'。主客队预期进球为 '+num(p.xg[0],2)+' 和 '+num(p.xg[1],2)+'。数据截止 '+fmtTime(p.cutoff)+'。'}
}
