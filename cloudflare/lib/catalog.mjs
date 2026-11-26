import { jsonBody } from './facts.mjs';

function localDay(stamp,zone) {
  return new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).format(stamp);
}
function dates(now,zone) {
  const today=localDay(now,zone), sat=new Date(today+'T12:00:00Z');
  sat.setUTCDate(sat.getUTCDate()+(sat.getUTCDay()===0?-1:(6-sat.getUTCDay()+7)%7));
  const sunday=new Date(sat);
  sunday.setUTCDate(sunday.getUTCDate()+1);
  return [sat,sunday].map(d=>d.toISOString().slice(0,10));
}
const entry=(title,text,id)=>({title,text,fact_ids:[id]});
export async function lookupCatalog(env,request,question,now,zone) {
  return queryCatalog(question,now,zone,async()=>{
    const res=await env.ASSETS.fetch(new Request(new URL('/analysis/catalog.json',request.url)));
    if (!res.ok || !res.headers.get('content-type')?.includes('json')) return null;
    return jsonBody(res,2_500_000);
  });
}
export async function queryCatalog(question,now,zone,load) {
  const schedule=/周末|下场踢|下一场|下轮对手|next match|weekend/i.test(question);
  const season=/20\d\d[-/]\d\d|上赛季|过去一季|试运行成绩|正式赛季/.test(question) && /模型|领先|市场|成绩|战绩|命中率|损失/.test(question);
  const leading=/领先市场|跑赢市场|比市场好|市场相比.*成绩|beat the market/i.test(question);
  if (!schedule && !season && !leading) return null;
  const catalog=await load();
  if (!catalog) return {sections:[],reason:'catalog_unavailable'};
  const sections=[], choices=[];
  if (season) {
    const pilot=question.includes('试运行'),official=question.includes('正式');
    const name=question.match(/20\d\d[-/]\d\d/)?.[0].replace('/','-') ||
      (pilot||official?Object.keys(catalog.seasons||{}).sort().reverse().find(s=>catalog.seasons[s][pilot?'pilot':'official']):catalog.last_complete_season);
    const row=catalog.seasons?.[name];
    for (const [kind,label] of [['pilot','试运行'],['official','正式评测']]) {
      const d=row?.[kind];
      if (!d || (pilot && kind!=='pilot') || (official && kind!=='official')) continue;
      const text=d.paired && Number.isFinite(d.model_loss) && Number.isFinite(d.market_loss)
        ? `${name} 赛季${label}有 ${d.paired} 场同期配对，融合模型 B 的平均对数损失为 ${d.model_loss.toFixed(4)}，市场为 ${d.market_loss.toFixed(4)}。`
        : `${name} 赛季${label}有 0 场可核验的同期市场配对。${d.sample_note||''}`;
      sections.push(entry(label,text,'season_'+kind));
    }
    if (row?.history && Number.isFinite(row.history.log_loss)) {
      sections.push(entry('历史回放',`${name} 赛季独立模型 A 的历史回放共 ${row.history.n} 场，平均对数损失为 ${row.history.log_loss.toFixed(4)}。`,'season_history'));
    }
    if (!sections.length) sections.push(entry('模型成绩','该赛季尚无可核验的同场市场比较。','season_missing'));
  } else if (leading) {
    const status={leading:'正式成绩在预注册赛季和时点达到领先门槛',unproven:'正式成绩尚未证实领先',accumulating:'正式评测正在积累样本'};
    const text=status[catalog.status] && Number.isSafeInteger(catalog.n_pairs)
      ? status[catalog.status]+'，可核验市场配对 '+catalog.n_pairs+' 场。' : '缺少已核验的正式市场比较结论。';
    sections.push(entry('模型成绩',text,'official_status'));
  } else {
    const q=question.toLowerCase(), teams=new Set();
    for (const [team,aliases] of Object.entries(catalog.aliases||{})) {
      if ([team,...aliases].some(s=>s && q.includes(s.toLowerCase()))) teams.add(team);
    }
    const weekend=/周末|weekend/i.test(question);
    const days=dates(now,zone);
    const games=(catalog.fixtures||[]).filter(f=>Date.parse(f.kickoff_utc)>now.valueOf() &&
      [...teams].every(t=>[f.home,f.away].includes(t)) && (!weekend || days.includes(localDay(new Date(f.kickoff_utc),zone))))
      .sort((a,b)=>Date.parse(a.kickoff_utc)-Date.parse(b.kickoff_utc));
    if (!games.length) sections.push(entry('赛程','已核实的当前赛季赛程中没有匹配的未来比赛。','schedule_missing'));
    else if (!teams.size && games.length>1) {
      for (const game of games.slice(0,8)) choices.push({id:game.id,label:game.home_zh+' – '+game.away_zh+' · '+game.kickoff_utc});
      sections.push(entry('赛程','请选择要查询的球队或场次。','schedule_choices'));
    } else {
      const game=games[0];
      sections.push(entry('赛程',game.home_zh+' 对 '+game.away_zh+'，开球时间（UTC）为 '+game.kickoff_utc+'。','schedule_next'));
      choices.push({id:game.id,label:game.home_zh+' – '+game.away_zh});
    }
  }
  if ((season||leading) && catalog.as_of) sections.push(entry('数据时点','数据截至 '+catalog.as_of+'。','catalog_as_of'));
  return {sections,choices,as_of:catalog.as_of,reason:'catalog_lookup'};
}
