import rules from '../../epl_agent/assets/data/fan_questions.json' with {type:'json'};
import {queryCatalog} from './catalog.mjs';

const patterns=rules.map(rule=>({...rule,regex:new RegExp(rule.pattern,'i')}));
const fixtureId=/^\d{4}-\d{2}_[a-z0-9-]+_[a-z0-9-]+$/;
const scopeTopics=new Set(['history','h2h','standing']);
const reviewTopics=new Set(['score','result','review']);
export function validContext(c){
  return c==null || (typeof c==='object' && fixtureId.test(c.fixture_id) && typeof c.topic==='string'
    && c.topic.length<=40 && (!c.fact_hash || /^[a-f0-9]{64}$/.test(c.fact_hash)));
}
function ruleFor(q,context){
  if(/不要剧透|不剧透|隐藏赛果/.test(q) && /赛前|预测/.test(q))return patterns.find(r=>r.topic==='probabilities');
  const prior=patterns.find(r=>r.topic===context?.topic);
  const direct=patterns.find(rule=>rule.regex.test(q));
  if(direct)return direct;
  if(/本页|这场呢|那场|那他们|那主队|那客队|主队呢|客队呢/.test(q)){
    if(/主队|客队/.test(q) && !['history','standing','probabilities','draw','form'].includes(prior?.topic))return patterns.find(r=>r.topic==='form');
    return prior?.topic==='schedule'?patterns.find(r=>r.topic==='kickoff'):prior;
  }
}
export function suggestedQuestions(facts) {
  const available=id=>facts.some(f=>f.id===id && !/^(暂无|缺少|尚无)/.test(f.text));
  return [
    available('favourite')?'这场更看好谁，领先多少？':null,
    available('home_form')&&available('away_form')?'两队近期的进攻表现如何？':null,
    available('top_scores')?'这场最可能的比分是什么？':null,
    available('market_gap')?'市场也这么看吗？':null,
    available('kickoff')?'这场几点开球？':null,
    available('expected_goals')?'本场预计进球是多少？':null
  ].filter(Boolean).slice(0,3);
}
function resolveSides(ctx,q,aliases){
  const sides=['home','away'];
  if(/主队/.test(q))return ['home'];
  if(/客队/.test(q))return ['away'];
  const low=q.toLowerCase();
  const teams=Object.keys(aliases||{}).filter(team=>[team,...aliases[team]].some(name=>low.includes(name.toLowerCase())));
  if(teams.some(t=>t!==ctx.home && t!==ctx.away))return [];
  const selected=sides.filter(side=>teams.includes(ctx[side]) || [ctx[side],ctx[side+'_zh']].some(name=>name && low.includes(name.toLowerCase())));
  return selected.length?selected:sides;
}
export async function answerFanQuestion({packet,question,context,now=new Date(),timezone='Europe/London',loadPacket,loadCatalog,loadHistory,loadReview}){
  if(!validContext(context))throw new Error('invalid_context');
  let target=packet;
  const page=packet.fixture_id;
  if(context && context.fixture_id!==page && !/本页|当前页|页面这场/.test(question)){
    target=await loadPacket(context.fixture_id);
    if(target.fixture_id!==context.fixture_id || (context.fact_hash && target.fact_hash!==context.fact_hash))throw new Error('409');
  }
  const rule=ruleFor(question,context);
  const topic=rule?.topic||'unknown';
  function answer(text,ids=[],missing=null,extra={}){
    return {fixture_id:page,fact_hash:packet.fact_hash,answer_fixture_id:target.fixture_id,answer_fact_hash:target.fact_hash,
      fixture_label:target.facts.find(f=>f.id==='fixture')?.text??target.fixture_id,
      answer_kickoff:target.facts.find(f=>f.id==='kickoff')?.text,as_of:target.as_of,evidence:ids,topic,source:'template',answer:text,missing_reason:missing,choices:[],...extra};
  }
  const missing=(message)=>answer(message,[],'missing_'+topic);
  if(!rule)return missing('当前资料无法回答这个问题。可以问本场概率、近期表现或比赛时间。');
  if(rule.unavailable)return missing(rule.unavailable);
  if(topic==='schedule'||topic==='model_results'){
    const catalog=await loadCatalog();
    const lookup=await queryCatalog(question,now,timezone,async()=>catalog);
    if(!lookup?.sections.length)return missing('暂缺可核对的赛程或成绩资料。');
    const choices=lookup.choices||[];
    if(topic==='schedule' && choices.length===1){
      target=choices[0].id===page?packet:await loadPacket(choices[0].id);
      if(target.fixture_id!==choices[0].id)throw new Error('fixture_mismatch');
    }
    const reason=lookup.sections[0].fact_ids[0];
    return answer(lookup.sections.map(s=>s.text).join(' '),lookup.sections.flatMap(s=>s.fact_ids),reason.includes('missing')?'missing_'+topic:null,
      {choices,as_of:lookup.as_of||target.as_of,needs_choice:reason==='schedule_choices'});
  }
  let facts=target.facts,asOf=target.as_of,ids=rule.ids||[],scope,standings;
  if(scopeTopics.has(topic)){
    scope=/现在|目前|最新|当前|截至今天|此刻/.test(question)?'current':'prematch';
    const info=await loadHistory(target.fixture_id,scope);
    if(!info || info.fixture_id!==target.fixture_id)return missing('本场缺少可核验的球队历史资料。');
    facts=info.facts;asOf=info.as_of;
    const sides=resolveSides(info,question,(await loadCatalog())?.aliases);
    if(!sides.length)return missing('提到的球队不在这场对阵中，请先查询该队的下一场比赛。');
    ids=topic==='h2h'?['h2h']:topic==='standing'&&sides.length===2&&!/两队|双方/.test(question)?['standings']:sides.map(side=>side+(topic==='history'?'_recent':'_standing'));
    if(ids.includes('standings'))standings=info.standings?.rows;
  }else if(reviewTopics.has(topic)){
    if(/不要剧透|不剧透|隐藏赛果|赛前分析/.test(question))return missing('赛前解读只使用开球前可见的事实。');
    const info=await loadReview(target.fixture_id);
    if(!info)return missing('当前缺少可核验的赛后记录。');
    facts=info.facts;asOf=info.as_of;
  }else if(topic==='form' && /主队|客队/.test(question)){
    ids=[question.includes('主队')?'home_form':'away_form'];
  }
  const used=ids.flatMap(id=>facts.filter(f=>f.id===id));
  if(!used.length)return missing('这场比赛暂缺可核对的对应资料。');
  const unavailable=used.every(f=>/缺少|尚无|暂无/.test(f.text));
  return answer(used.map(f=>f.text).join(' '),used.map(f=>f.id),unavailable?'missing_'+topic:null,{as_of:asOf,scope,standings});
}
