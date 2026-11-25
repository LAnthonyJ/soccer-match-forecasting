const tools = ['fixture','prediction','market','evaluation'];
const toolSet = new Set(tools);
export const fixtureId = /^[0-9]{4}-[0-9]{2}_[a-z0-9-]+_[a-z0-9-]+$/;
const hashPattern = /^[0-9a-f]{64}$/;
export const encoder = new TextEncoder();
export const canonical = value => {
  if (Array.isArray(value)) return '['+value.map(canonical).join(',')+']';
  if (value && typeof value==='object') {
    return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}';
  }
  return JSON.stringify(value);
};
const hex = buf => Array.from(new Uint8Array(buf),n=>n.toString(16).padStart(2,'0')).join('');
export const hash = async value => hex(await crypto.subtle.digest('SHA-256',encoder.encode(canonical(value))));
export async function visitorKey(ip, day, secret) {
  const key = await crypto.subtle.importKey('raw',encoder.encode(secret),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  return hex(await crypto.subtle.sign('HMAC',key,encoder.encode(day+'|'+ip)));
}
export class ApiError extends Error {
  constructor(status, reason) { super(reason); this.status=status; }
}
export async function jsonBody(res, max) {
  if (!res.body || Number(res.headers.get('content-length'))>max) throw new ApiError(413,'body_too_large');
  const reader = res.body.getReader(), chunks = [];
  let size=0;
  while (true) {
    const {done,value}=await reader.read();
    if (done) break;
    size+=value.byteLength;
    if (size>max) { await reader.cancel(); throw new ApiError(413,'body_too_large'); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset=0;
  for (const chunk of chunks) { bytes.set(chunk,offset); offset+=chunk.byteLength; }
  try { return JSON.parse(new TextDecoder().decode(bytes)); }
  catch { throw new ApiError(400,'invalid_json'); }
}

export async function validateFacts(data, id) {
  if (data?.schema!=='epl-analysis-facts/v1' || data.fixture_id!==id || !fixtureId.test(id) ||
      !Number.isSafeInteger(data.revision) || data.revision<0 || !hashPattern.test(data.fact_hash) ||
      !['frozen','preview','replay','reconstruction','schedule'].includes(data.source) ||
      typeof data.as_of!=='string' || !/(Z|\+00:00)$/.test(data.as_of) || !Number.isFinite(Date.parse(data.as_of)) ||
      !Array.isArray(data.facts) || data.facts.length<1 || data.facts.length>32) throw new ApiError(503,'invalid_facts');
  const ids=new Set();
  for (const f of data.facts) {
    if (!f || typeof f.id!=='string' || !/^[a-z][a-z0-9_.-]{0,63}$/.test(f.id) || ids.has(f.id) ||
        !toolSet.has(f.tool) || !['prematch','postmatch'].includes(f.stage) ||
        typeof f.text!=='string' || !f.text.trim() || f.text.length>320 ||
        Object.keys(f).some(k=>!['id','tool','text','stage'].includes(k))) throw new ApiError(503,'invalid_facts');
    ids.add(f.id);
  }
  const {schema,fixture_id,revision,source,facts}=data;
  if (await hash({schema,fixture_id,revision,source,facts})!==data.fact_hash) throw new ApiError(503,'invalid_facts');
  return data;
}

export async function loadFacts(env, request, id) {
  if (!fixtureId.test(id) || id.length>160) throw new ApiError(400,'invalid_fixture_id');
  const url=new URL('/analysis/facts/'+id+'.json',request.url);
  const res=await env.ASSETS.fetch(new Request(url));
  // Static SPA routing can return index.html on a missing asset.
  let packet;
  if (res.ok && res.headers.get('content-type')?.includes('json')) {
    packet=await jsonBody(res,96_000);
  } else if (res.status===404 || res.ok) {
    const archive=await env.ASSETS.fetch(new Request(new URL('/analysis/archive/'+id.slice(0,7)+'.json',request.url)));
    if (!archive.ok || !archive.headers.get('content-type')?.includes('json')) throw new ApiError(404,'fixture_not_found');
    packet=(await jsonBody(archive,16_000_000))[id];
    if (!packet) throw new ApiError(404,'fixture_not_found');
  } else throw new ApiError(503,'facts_unavailable');
  const validated=await validateFacts(packet,id);
  if (validated.facts.some(f=>f.stage!=='prematch')) throw new ApiError(503,'invalid_facts');
  return validated;
}

export async function reviewFacts(env,request,packet) {
  const res=await env.ASSETS.fetch(new Request(new URL('/analysis/reviews/'+packet.fixture_id.slice(0,7)+'.json',request.url)));
  if (!res.ok || !res.headers.get('content-type')?.includes('json')) return null;
  const row=(await jsonBody(res,8_000_000))[packet.fixture_id];
  if (!row) return null;
  if (!Array.isArray(row.facts) || row.facts.some(f=>f.stage!=='postmatch') || !row.facts.length ||
      !Number.isFinite(Date.parse(row.as_of)) || Date.parse(row.as_of)<Date.parse(packet.as_of)) throw new ApiError(503,'invalid_review');
  const {schema,fixture_id,revision,source}=packet;
  const extended={schema,fixture_id,revision,source,facts:[...packet.facts,...row.facts]};
  return validateFacts({...extended,as_of:row.as_of,fact_hash:await hash(extended)},fixture_id);
}

export async function isCurrent(env, request, id) {
  try {
    const res=await env.ASSETS.fetch(new Request(new URL('/analysis/current.json',request.url)));
    if (!res.ok) return false;
    const data=await jsonBody(res,8192);
    return data.schema==='epl-analysis-current/v1' && Array.isArray(data.fixture_ids) &&
      data.fixture_ids.length<=20 && data.fixture_ids.includes(id);
  } catch { return false; }
}

// The four tools can read only the already scoped fixture facts. No DB, URL or filesystem arguments.
export const readTools = Object.fromEntries(tools.map(tool=>[tool,(facts, ids)=>
  ids.map(id=>facts.find(f=>f.id===id && f.tool===tool))]));

export function viewFor(question) {
  return /赛后|复盘|完场|最终比分|结算|单场评分|得了多少分|post.?match|final result/i.test(question) &&
    !/赛前|隐藏赛果|别剧透|不要剧透|不剧透|pre.?match/i.test(question) ? 'postmatch' : 'prematch';
}
const titles={fixture:'赛程与时点',prediction:'赛果判断',market:'市场比较',evaluation:'模型成绩'};
export function renderCalls(calls, facts) {
  if (!Array.isArray(calls) || calls.length<1 || calls.length>4) throw new Error('unverified_analysis');
  const used=new Set();
  return calls.map(({tool,ids})=>{
    if (!toolSet.has(tool) || !Array.isArray(ids) || ids.length<1 || ids.length>5) throw new Error('unverified_analysis');
    const selected=readTools[tool](facts,ids);
    for (const f of selected) {
      if (!f || used.has(f.id)) throw new Error('unverified_analysis');
      used.add(f.id);
    }
    return {title:titles[tool],text:selected.map(f=>f.text).join(' '),fact_ids:selected.map(f=>f.id)};
  });
}

export function cutoffAnswer(facts, question) {
  if (!/截止|截点|窗口|冻结时|T[\s−–-]?24|cutoff|deadline/i.test(question)) return null;
  const field=facts.find(f=>f.id==='protocol_cutoff')??facts.find(f=>f.id==='kickoff');
  return field ? renderCalls([{tool:'fixture',ids:[field.id]}],facts) : null;
}
export function templateCalls(facts, question, kind) {
  const requested=/市场|盘口|赔率|分歧|market/i.test(question)?'market':
    /成绩|赛季|评分|复盘|结算|领先|得了多少分/i.test(question)?'evaluation':
    /赛程|几点|何时|时间|踢谁|周末/i.test(question)?'fixture':
    /模型|主胜|近况|概率|攻防|进球|why|预测|赢|看好|draw|平局|主队|客队|比分|主场|为什么/i.test(question)?'prediction':null;
  const order=kind==='report'?['prediction','fixture','market','evaluation']:[requested];
  return order.flatMap(tool=>{
    const ids=facts.filter(f=>f.tool===tool).slice(0,3).map(f=>f.id);
    return ids.length?[{tool,ids}]:[];
  });
}

export function completeReport(sections,facts) {
  const used=new Set(sections.flatMap(s=>s.fact_ids));
  for (const [title,ids] of [
    ['赛前判断',['probabilities','favourite']],['球队近况',['home_form','away_form','expected_goals']],
    ['市场对照',['market','market_gap','blended_gap']],['资料边界',['injuries']]
  ]) {
    const extra=ids.flatMap(id=>used.has(id)?[]:facts.filter(f=>f.id===id));
    if (extra.length) sections.push({title,text:extra.map(f=>f.text).join(' '),fact_ids:extra.map(f=>f.id)});
  }
  return sections;
}

export function validCached(saved,packet) {
  if (saved?.fixture_id!==packet.fixture_id || saved.fact_hash!==packet.fact_hash || saved.source!=='deepseek' ||
      !Array.isArray(saved.sections) || !saved.sections.length || saved.sections.length>8) return false;
  const used=new Set();
  for (const s of saved.sections) {
    if (!['赛程与时点','赛果判断','市场比较','模型成绩','赛前判断','球队近况','市场对照','资料边界'].includes(s.title) ||
        !Array.isArray(s.fact_ids) || !s.fact_ids.length || new Set(s.fact_ids).size!==s.fact_ids.length) return false;
    const facts=s.fact_ids.map(id=>packet.facts.find(f=>f.id===id));
    if (facts.some(f=>!f || used.has(f.id)) || s.text!==facts.map(f=>f.text).join(' ')) return false;
    facts.forEach(f=>used.add(f.id));
  }
  return saved.answer===saved.sections.map(s=>s.text).join('\n\n');
}

export function unsupported(question) {
  return /伤病|伤停|伤员|伤缺|受伤|健康状况|停赛|阵容|首发|天气|战术|教练布置|injur|line.?up|suspensi|weather|tactic/i.test(question);
}
