import assert from 'node:assert/strict'
import {after, before, test} from 'node:test'
import {spawn} from 'node:child_process'
import {once} from 'node:events'
import {mkdtemp, rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {createRequire} from 'node:module'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {onRequest} from '../../cloudflare/functions/api/analysis/ask.js'
import {F, A, STAMP, groups} from './fan_cases.mjs'

const here=path.dirname(fileURLToPath(import.meta.url))
const root=path.resolve(here,'../..')
const {chromium}=createRequire(path.join(root,'frontend/package.json'))('playwright')
let temp, processHandle, live, site, browser, page, selected, clickCount=0
const packets=new Map()

before(async()=>{
  temp=await mkdtemp(path.join(tmpdir(),'epl-fan-matrix-'))
  const env={...process.env,EPL_AGENT_HOME:path.join(temp,'db'),PYTHONUTF8:'1',OPENBLAS_NUM_THREADS:'1'}
  for(const key of ['EPL_ANALYSIS_ORIGIN','DEEPSEEK_API_KEY','GITHUB_TOKEN','ODDS_API_KEY']) delete env[key]
  processHandle=spawn(env.EPL_PYTHON||'python',['-B',path.join(here,'matchday_server.py'),'matrix',path.join(temp,'site')],
    {cwd:root,env,stdio:['ignore','pipe','pipe']})
  let stderr=''
  processHandle.stderr.on('data',s=>{stderr=(stderr+s).slice(-1500)})
  const port=await new Promise((resolve,reject)=>{
    const lines=createInterface({input:processHandle.stdout})
    const timeout=setTimeout(()=>reject(new Error('fixture export timeout '+stderr)),180000)
    const fail=code=>{clearTimeout(timeout);reject(new Error('fixture export exited '+code+' '+stderr))}
    processHandle.once('exit',fail)
    lines.on('line',line=>{
      if(!/^\d+$/.test(line))return
      clearTimeout(timeout);processHandle.off('exit',fail);lines.close();resolve(line)
    })
  })
  live='http://127.0.0.1:'+port
  site=live+'/static-batch'
  browser=await chromium.launch({headless:true,executablePath:process.env.EPL_BROWSER_PATH||undefined})
  page=await browser.newPage({locale:'zh-CN',timezoneId:'Asia/Shanghai',viewport:{width:390,height:844}})
  page.setDefaultTimeout(8000)
  await page.clock.setFixedTime(new Date(STAMP))
  for(const [id,percent] of [[F,'37.7'],[A,'44.0']]){
    const pkt=await (await fetch(site+'/analysis/facts/'+id+'.json')).json()
    assert.match(pkt.facts.find(f=>f.id==='probabilities').text,new RegExp(percent+'%'))
    packets.set(id,pkt)
  }
})
after(async()=>{
  await browser?.close()
  if(processHandle && processHandle.exitCode===null){
    const stopped=once(processHandle,'exit');processHandle.kill();await stopped
  }
  if(temp)await rm(temp,{recursive:true,force:true,maxRetries:3})
})

// The real Pages Function reads the same exported assets as the browser. No keys,
// external network, D1 balance or shared live issue are used in this test.
const edgeEnvironment={ASSETS:{fetch:request=>{
  const url=new URL(request.url)
  assert.equal(url.origin,'https://matrix.example')
  return fetch(site+url.pathname+url.search)
}}}
async function endpoint(kind,example,previous){
  const payload={fixture_id:example.page,fact_hash:packets.get(example.page).fact_hash,
    timezone:'Asia/Shanghai',question:example.question}
  if(previous)payload.context=previous
  const init={method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)}
  const res=kind==='Flask'?await fetch(live+'/api/analysis/ask',init):
    await onRequest({request:new Request('https://matrix.example/api/analysis/ask',init),env:edgeEnvironment})
  const body=await res.json()
  assert.equal(res.status,200,JSON.stringify(body))
  assert.equal(body.fixture_id,example.page,'page binding')
  assert.equal(body.fact_hash,payload.fact_hash,'page fact hash')
  return body
}
async function openAsk(id,reset=false){
  if(selected===id && !reset)return
  if(selected===id)await page.reload()
  else await page.goto(site+'/#/match/'+id)
  await page.getByRole('heading',{name:'胜平负预测'}).waitFor()
  await page.getByRole('button',{name:'问这场',exact:true}).click()
  await page.getByLabel('关于这场比赛的提问').waitFor()
  selected=id
}
async function renderedTurn(question){
  const turn=page.locator('.ai-turn').filter({has:page.getByText(question,{exact:true})}).last()
  await turn.waitFor()
  return {answer:await turn.locator('p').first().innerText(),
    answer_fixture_id:await turn.getAttribute('data-answer-fixture-id'),
    as_of:await turn.getAttribute('data-as-of'),topic:await turn.getAttribute('data-topic'),
    missing_reason:await turn.getAttribute('data-missing-reason')}
}
async function staticAnswer(example,reset){
  await openAsk(example.page,reset)
  await page.getByLabel('关于这场比赛的提问').fill(example.question)
  await page.getByRole('button',{name:'发送',exact:true}).click()
  return renderedTurn(example.question)
}
function verify(body,example){
  assert.equal(body.answer_fixture_id,example.expected,'wrong fixture for '+example.question)
  assert.equal(Date.parse(body.as_of),Date.parse(STAMP),'wrong evidence cutoff for '+example.question)
  assert.ok(body.topic,'question intent missing')
  for(const pattern of example.patterns) assert.match(body.answer,pattern,example.question+' => '+body.answer)
  if(example.exclude)assert.doesNotMatch(body.answer,example.exclude)
  if(example.missing) assert.equal(body.missing_reason,'missing_'+example.missing)
}

test('48 fixed questions use the same fixture, cutoff and values in all three products',async t=>{
  // The gateway obtains its clock when an ordinary Pages request enters onRequest.
  t.mock.timers.enable({apis:['Date'],now:Date.parse(STAMP)})
  assert.equal(groups.length,8)
  for(const [group,examples] of groups){
    assert.ok(examples.length>=6)
    const previous={}
    for(const [i,example] of examples.entries()){
      await t.test(group+' '+(i+1)+' '+example.question,async s=>{
        for(const kind of ['静态页面','Flask','Pages Function']){
          await s.test(kind,async()=>{
            const chained=group==='跨场指代'
            const answer=kind==='静态页面'?await staticAnswer(example,!chained||i===0):
              await endpoint(kind,example,chained?previous[kind]:null)
            verify(answer,example)
            if(kind!=='静态页面'){
              assert.equal(answer.answer_fact_hash,packets.get(example.expected).fact_hash)
              assert.ok(answer.missing_reason || answer.evidence?.length,JSON.stringify(answer))
            }
            previous[kind]={fixture_id:answer.answer_fixture_id,topic:answer.topic}
          })
        }
      })
    }
  }
})

test('every offered question on all ten audited fixtures gives match-specific evidence',async t=>{
  const board=await (await fetch(site+'/board.json')).json()
  assert.equal(board.fixtures.length,10)
  assert.ok(board.fixtures.every(f=>f.prediction?.source==='preview'))
  for(const game of board.fixtures){
    await t.test(game.id,async()=>{
      await openAsk(game.id,true)
      const buttons=page.locator('.ai-prompts button')
      const questions=await buttons.allTextContents()
      assert.ok(questions.length>0 && questions.length<=3)
      for(const [i,question] of questions.entries()){
        assert.doesNotMatch(question,/伤停|首发|停赛|赔率|市场|评分/)
        await buttons.nth(i).click()
        const turn=await renderedTurn(question)
        assert.equal(turn.answer_fixture_id,game.id)
        assert.equal(Date.parse(turn.as_of),Date.parse(STAMP))
        assert.ok(!turn.missing_reason,turn.answer)
        assert.doesNotMatch(turn.answer,/缺少|暂无|无法回答|尚未连接/)
        assert.match(turn.answer,/\d+(?:\.\d+)?\s*[%球分]/)
        clickCount++
      }
    })
  }
  t.diagnostic('Actual recommended questions clicked: '+clickCount)
})

test('an unspecified next match offers choices; selecting an opponent keeps the page and scopes the reply',async t=>{
  t.mock.timers.enable({apis:['Date'],now:Date.parse(STAMP)})
  const query={page:F,question:'下一场踢谁？'}
  const next={page:F,question:'那场几点开球？'}
  for(const kind of ['Flask','Pages Function']){
    const choices=await endpoint(kind,query)
    assert.equal(choices.needs_choice,true,kind)
    assert.ok(choices.choices.length>=2,kind)
    const chosen=choices.choices.find(c=>c.id!==F)
    const answer=await endpoint(kind,next,{fixture_id:chosen.id,topic:'schedule'})
    assert.equal(answer.answer_fixture_id,chosen.id)
    assert.equal(answer.as_of && Date.parse(answer.as_of),Date.parse(STAMP))
    assert.ok(answer.evidence.includes('kickoff'))
  }
  await staticAnswer(query,true)
  const choices=page.locator('.ai-turn').last().getByRole('button')
  assert.ok(await choices.count()>=2)
  // The second candidate has a different fixture ID to the first (Forest).
  await choices.nth(1).click()
  const answer=await renderedTurn(next.question)
  assert.notEqual(answer.answer_fixture_id,F)
  assert.ok(page.url().endsWith('#/match/'+F))
  assert.equal(await page.locator('.ai-turn').last().getByRole('link',{name:/打开这场比赛/}).getAttribute('href'),
    '#/match/'+answer.answer_fixture_id)
})
