export type Triple = [number, number, number]
export type Outcome = 'H'|'D'|'A'
export type Component = 'elo'|'dc'|'tree'|'a'
export type Probabilities = Record<Component,Triple|null>
export interface HistoryRow {
  id:string; date:string; season:string; home:string; away:string; actual:Outcome; cutoff:string; model_version:string; probs:Probabilities
}
export interface MarketRow {
  id:string; season:string; home?:string; away?:string; kickoff?:string|null; cutoff?:string|null;
  status?:string; reason?:string|null; actual?:string|number|null; a?:Triple|null; market?:Triple|null; b?:Triple|null; evidence?:string|null
}
export interface FixtureTrace {
  revision:number; schedule_at?:string; cutoff?:string; frozen?:string; witnessed?:string; result_at?:string;
  witness_status?:string; freeze_reason?:string; hash?:string; score_as_of?:string; settlement?:string;
  score?:{log_loss:number;brier:number;correct:boolean}; deployed?:{seen_at:string;sha:string};
  schedule_history:{revision:number;kickoff?:string;observed?:string;withdrawn?:boolean;evidence?:string}[]
}
export interface Independent {
  n_scored:number; total:number; eligible:number; finished:number; coverage:number; finished_coverage:number;
  scores:{log_loss:number|null;brier:number|null;accuracy:number|null}; rows:MarketRow[]; missing:Record<string,number>
}
export interface Prediction {
  a:Triple; b:Triple|null; market:Triple|null; xg:[number,number]; top_scores:[number,number,number][];
  cutoff:string; model_version:string; samples:[number,number];
  form?:Record<'home'|'away',{gf:number;ga:number;points:number}>; source:'replay'|'frozen'|'preview'|'reconstruction';
}
export interface Fixture {
  partial?:boolean;
  trace?:FixtureTrace; trace_answer?:string;
  schedule_status?:string; schedule_source?:string|null;
  score_sources?:Record<string,{observed_at?:string;score?:number[]|null}>;
  id:string; season:string; date:string; kickoff_utc:string|null; home:string; away:string; home_zh?:string; away_zh?:string;
  round?:string|number|null; hg:number|null; ag:number|null; prediction:Prediction|null;
  result_source?:string|null; source_conflict?:unknown; freeze_note?:string; market_note?:string; market_reason?:string; evidence?:string|null;
  explanation?:string; draw_explanation?:string; market_explanation?:string; independent_market_explanation?:string;
}
export interface SeasonScore {season:string; total:number; paired:number; coverage:number; model_loss:number|null; market_loss:number|null}
type MarketMetrics = Partial<Record<'a'|'market'|'b',{accuracy:number|null;log_loss:number|null;brier:number|null}>>
export interface Report {
  scores?:MarketMetrics;
  status:'accumulating'|'unproven'|'leading'; n_pairs:number; seasons:SeasonScore[];
  delta:number|null; ci:[number,number]|null; missing:Record<string,number>;
  independent?:Independent; pilot?:{n_pairs:number; scores?:MarketMetrics; missing?:Record<string,number>; rows?:MarketRow[]; seasons:SeasonScore[]; independent?:Independent}; rows?:MarketRow[];
  verification?:{pending_pairs?:number;audited_at?:string|null};
  history:{n:number; seasons:{season:string; n:number; log_loss:number; brier:number; accuracy:number; elo_loss:number}[];
    calibration:{p:number; observed:number; n:number}[]; worst:{id:string;home:string;away:string;season:string;prob:number;actual:string|number}[]};
}
export interface Health {
  settled_through?:string|null; latest_confirmed_score_date?:string|null;
  schedule_conflict_count?:number; schedule_unconfirmed_count?:number; schedule_pending_ids?:string[];
  season:string; last_scored_date:string|null; last_sync_at:string|null; overdue_count:number; overdue_ids:string[]; conflict_ids:string[]; conflict_count:number;
  sources?:{provider:string;last_success_at:string|null;status:string}[];
}
export interface Dashboard {
  compact?:boolean; week_catalog?:Record<string,string[]>; fixed_snapshot_url?:string;
  operations?:{worker_status:string; heartbeat_at?:string; snapshot_at?:string; next_cutoff?:string;
    upload_sha?:string; deployed_sha?:string; publication_status?:string};
  fixtures:Fixture[]; default_id:string; current_id?:string; updated_at:string; data_version?:string; available_seasons?:string[];
  health?:Health; public_snapshot?:boolean; archive_seasons?:string[]; last_scored_date?:string|null; last_complete_season?:string|null; team_aliases?:Record<string,string[]>; report:Report
}
export type Choice = {id:string; label:string}
export type Answer = {answer:string;choices?:Choice[]}
export const pct = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : (value*100).toFixed(1)+'%'
export const num = (n: number|null|undefined, d=3) => n == null || !Number.isFinite(n) ? '—' : n.toFixed(d)
export const sides = ['主胜','平局','客胜']
export const labels = {replay:'历史回放',frozen:'赛前已冻结',preview:'预测预览',reconstruction:'按历史时点重建'}
export const statusLabels = {accumulating:'积累样本中',unproven:'尚未证实领先',leading:'在所列赛季和时点领先'}
export const nameOf = (f: Fixture, s: 'home' | 'away') => f[s+'_zh' as 'home_zh'|'away_zh'] || f[s]
export function fixtureStatus(f: Fixture) {
  if(f.schedule_status==='conflict') return '开球时间冲突'
  if(!f.kickoff_utc) return '开球待核实'
  if(f.source_conflict) return '比分待核实'
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
export async function fetchBoard(signal:AbortSignal):Promise<Dashboard> {
  const response=await fetch(isStatic()?'./board.json':'/api/board',{signal})
  if(response.status===404)return fetchDash(undefined,signal)
  if(!response.ok)throw new Error('比赛看板暂不可用')
  const result=await response.json()
  if(!Array.isArray(result.fixtures)||!result.report?.history)throw new Error('比赛看板格式异常')
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
export async function fetchSeasonFixtures(season:string, signal:AbortSignal):Promise<Fixture[]> {
  const res=await fetch(isStatic()?'./seasons/'+encodeURIComponent(season)+'.json':'/api/seasons/'+encodeURIComponent(season),{signal})
  if(!isStatic()&&res.status===404) return (await fetchDash(season,signal)).fixtures
  if(!res.ok) throw new Error('该赛季暂不可用')
  return res.json()
}
export const reasons:Record<string,string> = {
  frozen:'赛前已冻结',scored:'已核验评分',missing_api_key:'采集机未配置赔率密钥',missing_witness:'缺少公开见证',
  late_witness:'见证晚于截止',pending_freeze:'等待冻结',prediction_error:'单场模型计算失败',deadline_reserve:'截止前未取得报价',
  missed_window:'错过采集窗口', no_matching_snapshot:'缺少对应版本快照', rescheduled:'赛程变更', pending_audit:'等待公开核验',
  insufficient_books:'合格书商不足', source_conflict:'比分冲突', unverified_forecast:'见证未通过', missing_market:'缺少同期市场',
  schedule_unverified:'赛程未核实', no_api_key:'采集机未配置赔率密钥', paired:'已配对', awaiting_result:'等待赛果',
  no_historical_market:'无同期市场', pending:'等待核验', duplicate_capture:'重复采集', no_snapshot:'缺少快照',
}
export function localAnswer(question:string,selected:Fixture|undefined,data:Dashboard):Answer {
  const q=question.trim().toLowerCase()
  if (/伤|停赛|首发|阵容|injur|lineup/.test(q)) return {answer:'目前缺少可核实的实时伤停和首发数据。模型使用赛前已知的历史赛果。'}
  const tracing=/这场得|这场评分|这场的评分|这场的成绩|结算|见证|时间线|这场比赛得|单场评分/.test(q)
  if (!tracing && /过去|成绩|表现比|领先市场|赛季模型|战绩|评测|试运行|历史回放|beat the market|上赛季/.test(q)) {
    const r=data.report
    const season=q.match(/20\d\d-\d\d/)?.[0] || (/过去一季|上赛季/.test(q)?data.last_complete_season:null)
    if(season){
      if(/历史|回放/.test(q)){
        const row=r.history.seasons.find(x=>x.season===season)
        return {answer:row?season+' 历史回放有 '+row.n+' 场，模型 A 平均对数损失 '+num(row.log_loss,4)+'，Brier 分数 '+num(row.brier,4)+'。':season+' 赛季尚无历史回放评分。'}
      }
      const official=r.seasons.find(x=>x.season===season)
      const pilot=r.pilot?.seasons.find(x=>x.season===season)
      const label=q.includes('正式')?'正式评测':q.includes('试运行')?'试运行':''
      const s=label==='正式评测'?official:label==='试运行'?pilot:official||pilot
      if(!s?.paired) return {answer:season+' 赛季'+label+'尚无可核验的同场市场配对，无法判断该季是否领先。'}
      return {answer:season+' 赛季'+(s===official?'正式评测':'试运行')+'有 '+s.paired+' 场有效配对，融合模型和市场平均对数损失分别为 '+num(s.model_loss,4)+'、'+num(s.market_loss,4)+'。'}
    }
    return {answer:'正式评测有 '+r.n_pairs+' 场可核验配对，目前'+statusLabels[r.status]+'。'}
  }
  const teams=new Set<string>()
  for(const [team,aliases] of Object.entries(data.team_aliases||{})) {
    if([team,...aliases].some(alias=>q.includes(alias.toLowerCase())))teams.add(team)
  }
  const weekend=/周末|weekend/.test(q), next=/下一场|下场/.test(q)
  let games=data.fixtures.filter(m=>[...teams].every(t=>t===m.home||t===m.away))
  if(weekend||next){
    const today=new Date();today.setHours(0,0,0,0)
    const sat=new Date(today);sat.setDate(today.getDate()+(today.getDay()===0?-1:(6-today.getDay()+7)%7))
    const end=new Date(sat);end.setDate(sat.getDate()+2)
    games=games.filter(m=>{const d=new Date(m.kickoff_utc||m.date+'T12:00:00');return m.hg===null&&(weekend?d>=sat&&d<end:m.kickoff_utc?+d>Date.now():d>=today)})
  }
  const f=next?games.slice().sort((a,b)=>a.date.localeCompare(b.date)||(a.kickoff_utc||'').localeCompare(b.kickoff_utc||'')||a.id.localeCompare(b.id))[0]
    :games.find(m=>m.id===selected?.id) || (games.length===1?games[0]:undefined)
  if(!f){
    const now=Date.now()
    games.sort((a,b)=>Math.abs(Date.parse(a.date)-now)-Math.abs(Date.parse(b.date)-now))
    return {answer:games.length?'请选择具体场次。':'已收录的赛程中没有找到符合条件的比赛。',choices:games.slice(0,8).map(m=>({id:m.id,label:m.date+' · '+m.home+' – '+m.away}))}
  }
  const title=f.date+' '+nameOf(f,'home')+' 对 '+nameOf(f,'away')
  if(tracing) return {answer:f.trace_answer||'本场没有可评分的前瞻冻结记录。'}
  if(next||/踢谁|对手|何时|时间|赛程|weekend/.test(q)) return {answer:title+'。开球时间（UTC）：'+(f.kickoff_utc||'待核实')+'。'}
  const marketQuestion=/市场|分歧|赔率|market/.test(q)
  if(!f.prediction){
    const note=marketQuestion?f.market_note:f.freeze_note
    return {answer:title+' 暂无已核实的赛前预测。'+(note?note+'。':'')}
  }
  if(marketQuestion){
    if(!f.prediction.market&&f.market_note)return {answer:title+'。'+f.market_note+'。缺少合格的同期市场快照，无法计算市场分歧。'}
    return {answer:title+'。'+(/独立|模型\s*a|model a/.test(q)?f.independent_market_explanation:f.market_explanation)}
  }
  return {answer:title+'。'+(q.includes('平局')?f.draw_explanation:f.explanation)}
}
