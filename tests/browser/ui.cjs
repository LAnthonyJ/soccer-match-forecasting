// Run from frontend with npm run test:browser. Both entry points use the shipped EPL data.
const { before, after, test } = require('node:test')
const assert = require('node:assert/strict')
const { createRequire } = require('node:module')
const { spawn, execFileSync } = require('node:child_process')
const { createServer } = require('node:http')
const { gzipSync } = require('node:zlib')
const { mkdtemp, readFile, rm, mkdir } = require('node:fs/promises')
const { tmpdir } = require('node:os')
const path = require('node:path')
const readline = require('node:readline')
const root = path.resolve(__dirname, '../..')
const { chromium, webkit } = createRequire(path.join(root, 'frontend/package.json'))('playwright')
const engine = process.env.EPL_BROWSER_ENGINE==='webkit'?webkit:chromium
let app, staticServer, browser, temp, live, pages
const snapshots = process.env.EPL_SCREENSHOTS
before(async () => {
  temp = await mkdtemp(path.join(tmpdir(), 'epl-browser-'))
  const site = path.join(temp, 'pages')
  const env = { ...process.env, EPL_AGENT_HOME:path.join(temp,'db'), PYTHONUTF8:'1' }
  const python = process.env.EPL_PYTHON || 'python'
  execFileSync(python,['-B','-m','epl_agent','export',site],{cwd:root,env,timeout:90000})
  const script = [
    'from werkzeug.serving import make_server',
    'from epl_agent.web import create_app',
    "srv=make_server('127.0.0.1',0,create_app(),threaded=True)",
    'print(srv.server_port,flush=True)',
    'srv.serve_forever()'
  ].join('\n')
  app = spawn(python,['-B','-c',script],{cwd:root,env,stdio:['ignore','pipe','inherit']})
  const port = await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Flask startup timed out')),30000)
    const rl=readline.createInterface({input:app.stdout})
    rl.on('line',line=>{if(/^\d+$/.test(line)){clearTimeout(timer);resolve(line);rl.close()}})
    app.once('exit',code=>{clearTimeout(timer);reject(new Error('Flask exited '+code))})
  })
  live='http://127.0.0.1:'+port
  staticServer=createServer(async(req,res)=>{
    const url=new URL(req.url,'http://localhost')
    if(!url.pathname.startsWith('/demo/')) return res.writeHead(404).end()
    const file=path.resolve(site,url.pathname.slice(6)||'index.html')
    if(!file.startsWith(site+path.sep)) return res.writeHead(403).end()
    try{
      const body=await readFile(file)
      const mime={'.js':'application/javascript','.css':'text/css','.html':'text/html','.json':'application/json'}
      const compressed=body.length>500 && /gzip/.test(req.headers['accept-encoding']||'')
      const data=compressed?gzipSync(body):body
      res.writeHead(200,{'content-type':(mime[path.extname(file)]||'application/octet-stream')+';charset=utf-8',
        ...(compressed?{'content-encoding':'gzip'}:{}),'content-length':data.length}).end(data)
    }catch{res.writeHead(404).end()}
  })
  await new Promise(resolve=>staticServer.listen(0,'127.0.0.1',resolve))
  pages='http://127.0.0.1:'+staticServer.address().port+'/demo/'
  browser=await engine.launch({headless:true,executablePath:process.env.EPL_BROWSER_PATH||undefined})
})
after(async()=>{
  await browser?.close()
  if(staticServer) await new Promise(resolve=>staticServer.close(resolve))
  if(app && app.exitCode===null){
    const done=new Promise(resolve=>app.once('exit',resolve));app.kill();await done
  }
  if(temp) await rm(temp,{recursive:true,force:true,maxRetries:4})
})
async function makePage(t,width,url) {
  const page=await browser.newPage({viewport:{width,height:width<650?840:960},isMobile:width<=768,hasTouch:width<=768,locale:'zh-CN',timezoneId:'Asia/Shanghai'})
  const errors=[]
  page.on('pageerror',err=>errors.push(err.message))
  t.after(async()=>{await page.close();assert.deepEqual(errors,[])})
  await page.goto(url)
  await page.getByRole('heading',{name:'胜平负预测'}).waitFor()
  return page
}
async function layout(page,width,filename){
  const size=await page.evaluate(()=>document.documentElement.scrollWidth)
  assert.ok(size<=width+1,'horizontal overflow: '+size+'/'+width)
  if(snapshots&&filename){
    await mkdir(snapshots,{recursive:true})
    await page.screenshot({path:path.join(snapshots,filename+'.png')})
  }
}

for(const width of [360,768,1440])test('archive navigation and schedule intent at '+width,async t=>{
  const page=await makePage(t,width,pages)
  const openYear=async year=>{
    if(width===360)await page.getByRole('button',{name:'赛程列表'}).click()
    await page.getByLabel('赛季',{exact:true}).selectOption(year)
    const game=page.locator('button.fixture').first()
    await game.waitFor()
    await game.click()
    await page.waitForURL(new RegExp('#/match/'+year+'_'))
    return page.url()
  }
  const first=await openYear('2002-03'),second=await openYear('2003-04')
  for(const [direction,url,year] of [['goBack',first,'2002-03'],['goForward',second,'2003-04']]){
    await page[direction]()
    await page.waitForFunction(({year,url})=>location.href===url&&document.querySelector('[aria-label="赛季"]').value===year,{year,url})
    await page.locator('.match-detail').waitFor()
    assert.match(await page.locator('.score-meta').innerText(),new RegExp(year.replace('-',' / ')))
  }
  await page.reload()
  await page.locator('.match-detail').waitFor()
  assert.equal(await page.getByLabel('赛季',{exact:true}).inputValue(),'2003-04')
  const future=await page.evaluate(async()=>{
    const data=await (await fetch('./board.json')).json()
    const season=await (await fetch('./seasons/'+data.health.season+'.json')).json()
    return season.filter(f=>(f.home==='Arsenal'||f.away==='Arsenal')&&f.hg===null)
      .sort((a,b)=>a.date.localeCompare(b.date)||a.id.localeCompare(b.id))
  })
  // Pin the visitor's clock to two known future fixtures from the shipped season.
  assert.ok(future.length>=3)
  await page.clock.install({time:new Date(future[0].date+'T00:00:00Z')})
  const later=future[2]
  await page.goto(pages+'#/match/'+later.id)
  await page.getByRole('link',{name:'预测助手'}).click()
  await page.getByLabel('你的问题').fill('阿森纳下一场踢谁')
  await page.getByRole('button',{name:/提问/}).click()
  await page.locator('.reply').filter({hasText:future[0].date}).waitFor()
  assert.ok(!(await page.locator('.reply').innerText()).includes(later.date))
})

if(Number(process.env.EPL_PERF_SAMPLES)>=10&&engine===chromium)test('mobile cold-load budgets',async t=>{
  const first=[],rows=[],count=Number(process.env.EPL_PERF_SAMPLES)
  for(let i=0;i<count;i++){
    const page=await browser.newPage({viewport:{width:360,height:840},isMobile:true,hasTouch:true,locale:'zh-CN',timezoneId:'Asia/Shanghai'})
    try{
      const requests=[]
      page.on('request',r=>requests.push(r.url()))
      const cdp=await page.context().newCDPSession(page)
      await cdp.send('Network.enable')
      await cdp.send('Network.setCacheDisabled',{cacheDisabled:true})
      await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:120,downloadThroughput:200000,uploadThroughput:90000})
      await cdp.send('Emulation.setCPUThrottlingRate',{rate:4})
      await page.goto(pages,{waitUntil:'domcontentloaded'})
      await page.getByRole('heading',{name:'胜平负预测'}).waitFor()
      first.push(Math.round(await page.evaluate(()=>performance.now())))
      assert.ok(requests.every(u=>!u.endsWith('/dashboard.json')&&!u.includes('/seasons/')))
      const start=await page.evaluate(()=>performance.now())
      await page.getByRole('link',{name:'模型成绩'}).click()
      await page.locator('.rp-record-table tbody tr').nth(19).waitFor()
      rows.push(Math.round((await page.evaluate(()=>performance.now()))-start))
      assert.ok(!requests.some(u=>u.endsWith('/history-rows.json')))
    }finally{await page.close()}
  }
  const p95=xs=>[...xs].sort((a,b)=>a-b)[Math.ceil(xs.length*.95)-1]
  t.diagnostic(JSON.stringify({count,first,rows,firstP95:p95(first),rowsP95:p95(rows)}))
  assert.ok(p95(first)<=2500,'First forecast exceeded 2500 ms')
  assert.ok(p95(rows)<=3000,'First 20 rows exceeded 3000 ms')
})
test('the no-key report cites real pre-match evidence and a changed version requires refresh',async t=>{
  for(const url of [live,pages]) {
    const page=await makePage(t,360,url)
    await page.getByRole('button',{name:'阅读 AI 赛事分析'}).click()
    const panel=page.getByRole('region',{name:'赛事分析'})
    await panel.getByText(/基础数据解读 · 模板生成/).waitFor()
    await panel.getByRole('heading',{name:'球队近况'}).waitFor()
    const evidence=await panel.getByRole('link',{name:'核对本场分析依据'}).getAttribute('href')
    if(url===live){
      const prompt=panel.getByLabel('关于这场比赛的追问')
      await prompt.fill('这场的伤停情况？')
      await panel.getByRole('button',{name:'发送追问'}).click()
      await panel.locator('.ai-turn').getByText(/缺少可核实的实时伤停/).waitFor()
      await page.route('**/api/analysis/ask',route=>route.fulfill({status:409,json:{error:'facts_changed'}}))
      await prompt.fill('继续解释')
      await panel.getByRole('button',{name:'发送追问'}).click()
      await panel.getByText(/比赛资料已更新/).waitFor()
      await panel.getByRole('button',{name:'重新读取报告'}).click()
      await panel.getByRole('heading',{name:'赛前判断'}).waitFor()
    }
    await page.goto(new URL(evidence,page.url()).toString())
    await page.locator('li').filter({hasText:'独立模型 A 给出'}).waitFor()
    assert.equal(await page.locator('li').filter({hasText:'已收录赛果'}).count(),0)
  }
})
test('local and Pages show the same match explanation and last full season',async t=>{
  const replies=[]
  for(const url of [live,pages]){
    const page=await makePage(t,360,url)
    await page.getByRole('button',{name:'看本轮'}).click()
    await page.getByRole('button',{name:'体验历史回放'}).click()
    await page.getByRole('button',{name:/询问这场比赛的预测依据/}).click()
    const reply=page.locator('.reply')
    await reply.filter({hasText:'4 场联赛先验'}).waitFor()
    const text=await reply.innerText()
    await page.getByRole('button',{name:'过去一季模型比市场好吗？'}).click()
    await reply.filter({hasText:'无法判断'}).waitFor()
    replies.push([text,await reply.innerText()])
  }
  assert.deepEqual(replies[0],replies[1])
})
for(const width of [360,768,1440]) test('live replay and navigation at '+width,async t=>{
  const page=await makePage(t,width,live)
  await layout(page,width,width===1440?'matchdesk-desktop':width===360?'matchdesk-mobile':'')
  await page.getByRole('button',{name:'查看真实赛果'}).click()
  await page.getByRole('button',{name:'隐藏赛果'}).waitFor()
  if(width===360) await page.getByRole('button',{name:'赛程列表'}).click()
  await page.getByLabel('搜索球队').fill('阿森纳')
  const items=page.locator('button.fixture')
  await items.first().waitFor()
  assert.match(await items.first().innerText(),/阿森纳/)
  await items.first().click()
  await page.getByRole('heading',{name:/阿森纳/}).waitFor()
  await layout(page,width)
  await page.getByRole('link',{name:'模型成绩'}).click()
  await page.getByRole('heading',{name:'逐季历史成绩'}).waitFor()
  assert.ok(await page.locator('.report table tbody tr').count()>10)
  await layout(page,width,width===1440?'model-report':'')
})
test('static Pages subpath, offline answers, unavailable market and source',async t=>{
  const page=await makePage(t,1440,pages)
  const queried=[];page.on('request',r=>queried.push(r.url()))
  await page.getByRole('button',{name:/询问这场比赛的预测依据/}).click()
  await page.locator('.reply').filter({hasText:'独立模型 A'}).waitFor()
  await page.getByRole('button',{name:'模型和市场的分歧在哪里？'}).click()
  await page.locator('.reply').filter({hasText:'缺少合格'}).waitFor()
  assert.ok(queried.every(url=>!url.includes('/api/')))
})
test('Pages distinguishes a missed freeze from absent odds in the detail and assistant',async t=>{
  const page=await makePage(t,768,pages)
  await page.route('**/board.json',async route=>{
    const response=await route.fetch(), data=await response.json()
    const game=data.fixtures.find(f=>f.id===data.default_id)
    Object.assign(game,{prediction:null,freeze_note:'错过赛前 24 小时采集窗口',market_note:'采集机未配置赔率密钥'})
    await route.fulfill({response,json:data})
  })
  await page.reload()
  const panel=page.locator('section').filter({has:page.getByRole('heading',{name:'暂无合格预测'})})
  await panel.getByText('错过赛前 24 小时采集窗口',{exact:true}).waitFor()
  await panel.getByText('缺少市场报价',{exact:true}).click()
  await panel.getByText('采集机未配置赔率密钥',{exact:true}).waitFor()
  await page.getByRole('link',{name:'预测助手'}).click()
  for(const [question,expected,other] of [
    ['为什么没有预测','错过赛前 24 小时采集窗口','赔率密钥'],
    ['为什么没有市场报价','采集机未配置赔率密钥','错过赛前'],
  ]){
    await page.getByLabel('你的问题').fill(question)
    await page.getByRole('button',{name:/提问/}).click()
    const reply=page.locator('.reply').filter({hasText:expected})
    await reply.waitFor()
    assert.ok(!(await reply.innerText()).includes(other))
  }
})
test('older seasons, keyboard navigation, real agent',async t=>{
  const page=await makePage(t,1440,live)
  await page.getByLabel('赛季',{exact:true}).selectOption('2020-21')
  await page.getByLabel('搜索球队').fill('利物浦')
  await page.locator('button.fixture').first().waitFor()
  await page.locator('button.fixture').first().focus()
  await page.keyboard.press('Enter')
  await page.getByRole('heading',{name:/利物浦/}).waitFor()
  await page.getByRole('button',{name:/询问这场比赛的预测依据/}).click()
  await page.locator('.reply').filter({hasText:/主胜/}).waitFor()
  await page.getByRole('button',{name:'这场比赛有哪些伤停？'}).click()
  await page.locator('.reply').filter({hasText:'缺少'}).waitFor()
})
test('delayed older request cannot overwrite new selection',async t=>{
  const page=await makePage(t,1440,live)
  await page.waitForLoadState('networkidle')
  const items=page.locator('button.fixture')
  const [one,two]=await items.evaluateAll(nodes=>nodes.flatMap((button,index)=>
    button.getAttribute('aria-pressed')==='true'?[]:[{index,teams:Array.from(
      button.querySelectorAll('.fixture-teams>span'),s=>s.textContent.trim())}]))
  assert.notDeepEqual(one.teams,two.teams)
  let release,started
  const held=new Promise(resolve=>{release=resolve})
  const olderReady=new Promise(resolve=>{started=resolve})
  // Deliver the old response even when the application aborts it. This exercises
  // the selection guard independently of the browser's transport cancellation.
  await page.evaluate(()=>{
    const nativeFetch=window.fetch.bind(window)
    window.fetch=(input,init)=>nativeFetch(input,String(input).includes('/api/fixtures/')?{...init,signal:null}:init)
  })
  await page.route('**/api/fixtures/*',async route=>{
    const response=await route.fetch()
    started(route.request().url())
    await held
    await route.fulfill({response})
  },{times:1})
  try {
    await items.nth(one.index).click()
    const olderUrl=await olderReady
    const newerResponse=page.waitForResponse(res=>res.url().includes('/api/fixtures/')&&res.url()!==olderUrl)
    await items.nth(two.index).click()
    const newer=await (await newerResponse).json()
    assert.deepEqual([newer.home_zh,newer.away_zh],two.teams)
    await page.waitForFunction(expected=>Array.from(document.querySelectorAll('.matchup .side>span:nth-child(2)'),
      s=>s.textContent).join('|')===expected,two.teams.join('|'))
    const delayedResponse=page.waitForResponse(olderUrl)
    release()
    await delayedResponse
  }finally{release()}
  await page.waitForLoadState('networkidle')
  assert.deepEqual(await page.locator('.matchup .side>span:nth-child(2)').allTextContents(),two.teams)
})
test('network failure can be retried; supplied comparison uses percentage points',async t=>{
  const page=await browser.newPage()
  t.after(()=>page.close())
  let first=true
  await page.route('**/api/board',route=>{
    if(first){first=false;return route.abort('failed')}
    return route.fetch().then(async response=>{
      const data=await response.json(),p=data.fixtures.find(m=>m.id===data.default_id).prediction
      p.b=[0.40,0.25,0.35];p.market=[0.35,0.25,0.40]
      return route.fulfill({response,json:data})
    })
  })
  await page.route('**/api/fixtures/*',async route=>{
    const response=await route.fetch()
    const game=await response.json()
    game.prediction.b=[0.40,0.25,0.35]
    game.prediction.market=[0.35,0.25,0.40]
    await route.fulfill({json:game})
  })
  await page.goto(live)
  await page.getByRole('alert').waitFor()
  await page.getByRole('button',{name:'重试'}).click()
  await page.getByText(/相差 5.0 个百分点/).waitFor()
  assert.match(await page.locator('.chart-row.market').innerText(),/35.0%/)
  assert.match(await page.locator('.chart-row.model-b').innerText(),/40.0%/)
})

test('report filters a real season, separates draw calibration and downloads the selected matches',async t=>{
  const page=await makePage(t,768,pages)
  await page.getByRole('link',{name:'模型成绩'}).click()
  await page.getByLabel('诊断赛季').selectOption('2024-25')
  await page.getByLabel('校准模型').selectOption('dc')
  await page.getByRole('button',{name:'平局',exact:true}).click()
  await page.getByText('平局分箱明细',{exact:true}).click()
  const bins=page.locator('.rp-bins tbody tr')
  assert.equal(await bins.count(),10)
  const counts=await bins.locator('td:last-child').allTextContents()
  assert.equal(counts.reduce((n,x)=>n+Number(x),0),380)
  await page.getByLabel('记录赛季').selectOption('2024-25')
  await page.getByLabel('场次筛选').selectOption('worst')
  const records=page.locator('.rp-record-table tbody tr')
  await records.nth(9).waitFor()
  assert.equal(await records.count(),10)
  const down=page.waitForEvent('download')
  await page.getByRole('button',{name:/下载当前筛选 CSV/}).click()
  const file=await down
  assert.equal(file.suggestedFilename(),'epl-history-2024-25.csv')
  const lines=(await readFile(await file.path(),'utf8')).trim().split(/\r?\n/)
  assert.equal(lines.length,11)
  assert.match(lines[0],/"a_D"/)
  assert.ok(lines.slice(1).every(x=>x.includes('"2024-25"')))
  await records.first().getByRole('button').click()
  await page.getByRole('heading',{name:'胜平负预测'}).waitFor()
  await layout(page,768)
})
test('report drills from a witnessed pilot example to per-match scores, exclusions and replication inputs',async t=>{
  const page=await makePage(t,1440,pages)
  await page.route('**/board.json',async route=>{
    const response=await route.fetch(), data=await response.json()
    const m=data.fixtures.find(f=>f.prediction&&f.hg!==null)
    const base={id:m.id,season:m.season,home:m.home,away:m.away,kickoff:m.kickoff_utc}
    const paired={...base,status:'paired',reason:'paired',actual:'D',a:[.4,.35,.25],b:[.32,.39,.29],market:[.3,.4,.3],evidence:'https://github.com/test/test/issues/1#issuecomment-123'}
    data.fixed_snapshot_url='https://raw.githubusercontent.com/test/test/'+'a'.repeat(40)+'/snapshot.json'
    data.report.pilot={n_pairs:1,seasons:[],rows:[paired,{...base,id:'test-conflict',reason:'source_conflict',status:'excluded'}]}
    data.public_snapshot=true
    await page.route('**/report.json',async r=>{const full=await (await r.fetch()).json();await r.fulfill({json:{...full,pilot:data.report.pilot}})})
    await route.fulfill({response,json:data})
  })
  await page.reload()
  await page.getByRole('link',{name:'模型成绩'}).click()
  const proof=page.locator('section').filter({has:page.getByRole('heading',{name:'首场公开核验'})})
  assert.equal(await proof.getByRole('link',{name:/本场赛前公开见证/}).getAttribute('href'),'https://github.com/test/test/issues/1#issuecomment-123')
  assert.match(await proof.locator('tbody tr').first().innerText(),/1\.0498/)
  assert.equal(await proof.getByRole('link',{name:/预测快照 JSON/}).getAttribute('href'),'https://raw.githubusercontent.com/test/test/'+'a'.repeat(40)+'/snapshot.json')
  await page.getByLabel('记录来源').selectOption('pilot')
  await page.getByLabel('缺失原因或状态').selectOption('source_conflict')
  assert.equal(await page.locator('.rp-record-table tbody tr').count(),1)
  assert.match(await page.locator('.rp-record-table tbody tr').innerText(),/比分待核实/)
  await proof.getByRole('button').click()
  await page.getByRole('heading',{name:'胜平负预测'}).waitFor()
})

for (const width of [360,768,1440]) {
  test('verified independent forecast, timeline, reason and CSV at '+width,async t=>{
    const page=await makePage(t,width,pages)
    let fixtureId
    await page.route('**/board.json',async route=>{
      const response=await route.fetch(), data=await response.json()
      const game=data.fixtures.find(m=>m.id===data.default_id)
      fixtureId=game.id
      game.evidence='https://github.com/example/repo/issues/1#issuecomment-1'
      game.trace={revision:2,schedule_history:[],score:{log_loss:0.9163,brier:0.545,correct:true},
        deployed:{seen_at:game.kickoff_utc,sha:'a'.repeat(40)}}
      game.market_note='采集机未配置赔率密钥'
      data.report.pilot={n_pairs:0,seasons:[],independent:{n_scored:1,total:380,eligible:310,finished:1,
        coverage:1/310,finished_coverage:1,scores:{accuracy:1,log_loss:.9163,brier:.545},missing:{},
        rows:[{id:game.id,season:game.season,home:game.home,away:game.away,kickoff:game.kickoff_utc,
          actual:'H',a:[.4,.35,.25],status:'scored',reason:'scored',evidence:'https://github.com/example/repo/issues/1#issuecomment-1'}]}}
      await page.route('**/report.json',async r=>{const full=await (await r.fetch()).json();await r.fulfill({json:{...full,pilot:data.report.pilot}})})
      await route.fulfill({response,json:data})
    })
    await page.reload()
    await page.getByRole('link',{name:'模型成绩'}).click()
    await page.getByRole('button',{name:'试运行',exact:true}).click()
    await page.getByRole('heading',{name:'独立模型 A 前瞻成绩'}).waitFor()
    await page.getByLabel('记录来源').selectOption('independent')
    assert.equal(await page.locator('.rp-record-table tbody tr').count(),1)
    const downloaded=page.waitForEvent('download')
    await page.getByRole('button',{name:/下载当前筛选 CSV/}).click()
    assert.match(await readFile(await (await downloaded).path(),'utf8'),new RegExp(fixtureId))
    await page.locator('.rp-record-table tbody button').click()
    await page.getByText(/比赛时间线 · 赛程版本/).click()
    await page.getByText(/模型 A 本场对数损失/).waitFor()
    assert.equal(await page.getByRole('link',{name:/本版比赛记录的固定数据提交/}).getAttribute('href'),
      'https://github.com/example/repo/commit/'+'a'.repeat(40))
    await page.getByText('缺少市场报价',{exact:true}).click()
    await page.getByText('采集机未配置赔率密钥',{exact:true}).first().waitFor()
    await layout(page,width)
  })
}
