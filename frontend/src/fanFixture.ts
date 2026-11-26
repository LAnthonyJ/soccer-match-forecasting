import { nameOf, type Fixture } from './data'

export function fanPhase(f:Fixture,now=Date.now()) {
  if(f.source_conflict) return 'conflict'
  if(f.hg!=null && f.ag!=null) return 'finished'
  const kickoff=Date.parse(f.kickoff_utc||'')
  if(!Number.isFinite(kickoff) || f.schedule_status==='unconfirmed' || f.schedule_status==='conflict') return 'unscheduled'
  return kickoff<=now?'waiting':'scheduled'
}

export function localZoneLabel() {
  const zone=Intl.DateTimeFormat().resolvedOptions().timeZone
  return zone==='Asia/Shanghai' || zone==='Asia/Chongqing'?'北京时间':zone
}

export function forecastLine(f:Fixture,compact=false) {
  const chances=f.prediction?.a
  if(!chances) return ''
  const percents=chances.map(v=>Number((v*100).toFixed(1)))
  const order=[0,1,2].sort((i,j)=>percents[j]-percents[i])
  const [first,next]=order
  const outcomes=[nameOf(f,'home')+'胜','平局',nameOf(f,'away')+'胜']
  const gap=percents[first]-percents[next]
  const tied=order.filter(i=>percents[first]===percents[i])
  if(tied.length>1) return tied.length===3?'三项概率并列':tied.map(i=>outcomes[i]).join('与')+'并列居首'
  if(compact) {
    if(first!==1 && next!==1 && gap<5) return '两队接近'
    return first===1?'平局概率最高':nameOf(f,first===0?'home':'away')+(gap<5?'略占先':'胜率居首')
  }
  const close=gap<5 && first!==1 && next!==1?'，两队接近':''
  return outcomes[first]+'比'+outcomes[next]+'高 '+gap.toFixed(1)+' 个百分点'+close+'。'
}
