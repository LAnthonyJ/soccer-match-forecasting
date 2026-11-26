// Run from frontend with npm run test:browser. Both entry points use the shipped EPL data.
const { before, after, test } = require('node:test')
const assert = require('node:assert/strict')
const { createRequire } = require('node:module')
const { spawn, execFileSync } = require('node:child_process')
const { createServer } = require('node:http')
const { gzipSync } = require('node:zlib')
const { mkdtemp, readFile, rm } = require('node:fs/promises')
const { tmpdir } = require('node:os')
const path = require('node:path')
const readline = require('node:readline')
const root = path.resolve(__dirname, '../..')
const { chromium, webkit } = createRequire(path.join(root, 'frontend/package.json'))('playwright')
const engine = process.env.EPL_BROWSER_ENGINE==='webkit'?webkit:chromium
let app, staticServer, browser, temp, live, pages
before(async () => {
  temp = await mkdtemp(path.join(tmpdir(), 'epl-browser-'))
  const site = path.join(temp, 'pages')
  const env = { ...process.env, EPL_AGENT_HOME:path.join(temp,'db'), PYTHONUTF8:'1' }
  const python = process.env.EPL_PYTHON || 'python'
  const harness=path.join(__dirname,'matchday_server.py')
  execFileSync(python,['-B',harness,'export',site],{cwd:root,env,timeout:180000})
  app = spawn(python,['-B',harness,'serve'],{cwd:root,env,stdio:['ignore','pipe','inherit']})
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

const FOREST='2026-27_nottingham-forest_chelsea'
const ARSENAL='2026-27_arsenal_manchester-city'
const PAST='2026-27_fulham_manchester-united'
const STAMP='2026-11-26T08:00:00Z'
async function open(t,width,url) {
  const page=await browser.newPage({viewport:{width,height:width===360?800:960},isMobile:width<=768,
    hasTouch:width<=768,locale:'zh-CN',timezoneId:'Asia/Shanghai'})
  page.setDefaultTimeout(8000)
  const errors=[]
  page.on('pageerror',err=>errors.push(err.message))
  t.after(async()=>{await page.close();assert.deepEqual(errors,[])})
  await page.clock.setFixedTime(new Date(STAMP))
  if(url)await page.goto(url)
  return page
}
async function fits(page,width) {
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth)<=width+1,'horizontal overflow')
}
const row=(p,id)=>p.locator('[data-fixture-id="'+id+'"]')
for(const width of [360,768,1440]) test('matchweek, forecast, results and permalink at '+width,async t=>{
  const p=await open(t,width)
  const requests=[];p.on('request',r=>requests.push(r.url()))
  await p.goto(pages)
  const rows=p.getByTestId('fixture-row')
  await rows.nth(1).waitFor()
  assert.equal(await rows.count(),10)
  assert.equal(await p.getByRole('heading',{name:'英超赛程',exact:true}).count(),1)
  assert(await rows.nth(1).boundingBox().then(b=>b.y+b.height<=p.viewportSize().height))
  assert(!requests.some(u=>/\/(seasons|fixtures|history)\//.test(u)||/dashboard.json|report.json/.test(u)))
  assert.equal(await p.getByRole('button',{name:'体验历史回放'}).count(),0)
  await fits(p,width)
  const search=p.getByRole('searchbox').or(p.getByLabel('搜索球队',{exact:true})).first()
  await search.fill('Chelsea')
  await row(p,FOREST).waitFor()
  assert.equal(await rows.count(),1)
  await row(p,FOREST).click()
  await p.waitForURL(new RegExp(FOREST))
  await p.getByRole('heading',{name:'胜平负预测'}).waitFor()
  const probs=p.getByRole('heading',{name:'胜平负预测'})
  assert.ok((await probs.boundingBox()).y<p.viewportSize().height)
  const probabilityCells=p.locator('.fan-odds strong')
  assert.equal(await probabilityCells.count(),3)
  for(const cell of await probabilityCells.all()){
    const box=await cell.boundingBox()
    assert.ok(box && box.y+box.height<=p.viewportSize().height)
  }
  await fits(p,width)
  await p.goBack()
  await row(p,FOREST).waitFor()
  assert.equal(await search.inputValue(),'Chelsea')
  await search.fill('')
  await p.getByRole('button',{name:'上一轮',exact:true}).click()
  await p.waitForFunction(()=>!document.querySelector('[data-fixture-id="2026-27_nottingham-forest_chelsea"]'))
  await p.getByRole('button',{name:'返回本轮',exact:true}).click()
  await row(p,FOREST).waitFor()
  await p.goto(pages+'#/match/'+PAST)
  await p.getByRole('article',{name:'比赛详情'}).getByText('已完场',{exact:true}).waitFor()
  assert.equal(await p.getByRole('heading',{name:'胜平负预测'}).count(),0)
  assert.equal(await p.getByRole('button',{name:'查看真实赛果'}).count(),0)
  await p.reload()
  await p.getByRole('article',{name:'比赛详情'}).getByText('已完场',{exact:true}).waitFor()
  await fits(p,width)
})
test('same-match local and static analysis retain drafts and history; injuries disclose missing data',async t=>{
  for(const url of [live,pages]){
    const p=await open(t,360,url+'#/match/'+FOREST)
    await p.getByRole('heading',{name:'胜平负预测'}).waitFor()
    await p.getByRole('button',{name:'问这场',exact:true}).click()
    const input=p.getByLabel('关于这场比赛的提问')
    await input.fill('这场几点开球？')
    await p.getByRole('button',{name:'发送',exact:true}).click()
    await p.locator('.ai-turn').filter({hasText:/11\/28 04:00/}).filter({hasText:'北京时间'}).waitFor()
    await input.fill('这场的实时伤停情况？')
    await p.getByRole('button',{name:'发送',exact:true}).click()
    await p.locator('.ai-turn').filter({hasText:'缺少'}).waitFor()
    await input.fill('还有哪些依据？')
    const close=p.getByRole('button',{name:/收起.*(提问|问答|追问)|问这场/}).last()
    await close.click()
    await p.getByRole('button',{name:'问这场',exact:true}).click()
    assert.equal(await input.inputValue(),'还有哪些依据？')
    await p.locator('.ai-turn').filter({hasText:'缺少'}).waitFor()
    await p.goto(url+'#/match/'+ARSENAL)
    await p.getByRole('heading',{name:'胜平负预测'}).waitFor()
    await p.getByRole('button',{name:'问这场',exact:true}).click()
    assert.equal(await input.inputValue(),'')
    assert.equal(await p.locator('.ai-turn').count(),0)
  }
})

test('keyboard date filtering and mobile return restore the selected row and scroll offset',async t=>{
  const p=await open(t,360,pages)
  const date=p.getByTestId('fixture-date').first().getByRole('button').first()
  await date.focus()
  await p.keyboard.press('Enter')
  await p.waitForURL(/date=/)
  assert.equal(await p.getByTestId('fixture-date').count(),1)
  await p.getByRole('button',{name:/清除/}).click()
  const last=p.getByTestId('fixture-row').last()
  await last.scrollIntoViewIfNeeded()
  const before=await p.evaluate(()=>window.scrollY)
  assert.ok(before>50)
  const fid=await last.getAttribute('data-fixture-id')
  await last.click()
  await p.getByRole('article',{name:'比赛详情'}).waitFor()
  await p.getByTestId('back-to-fixtures').click()
  await row(p,fid).waitFor()
  assert.equal(await p.evaluate(()=>document.activeElement?.getAttribute('data-fixture-id')),fid)
  assert.ok(Math.abs(await p.evaluate(()=>window.scrollY)-before)<=3)
})
test('network interruption keeps deep link, retry submits original question and changed facts require refresh',async t=>{
  const p=await open(t,360)
  await p.route('**/api/matches/'+FOREST,r=>r.abort('failed'),{times:1})
  await p.goto(live+'#/match/'+FOREST)
  await p.getByRole('button',{name:/重试/}).first().click()
  await p.getByRole('heading',{name:'胜平负预测'}).waitFor()
  assert(p.url().endsWith(FOREST))
  await p.getByRole('button',{name:'问这场',exact:true}).click()
  const input=p.getByLabel('关于这场比赛的提问')
  const question='双方为什么接近？'
  await input.fill(question)
  await p.route('**/api/analysis/ask',r=>r.abort('failed'),{times:1})
  await p.getByRole('button',{name:'发送',exact:true}).click()
  await p.getByRole('alert').last().waitFor()
  assert.equal(await input.inputValue(),question)
  const receipt=p.waitForRequest(r=>r.url().endsWith('/api/analysis/ask'))
  await p.getByRole('button',{name:/重试原问题|重试追问|重新发送|重试/}).last().click()
  assert.equal((await receipt).postDataJSON().question,question)
  await p.locator('.ai-turn').first().waitFor()
  await p.route('**/api/analysis/ask',r=>r.fulfill({status:409,json:{error:'version_changed'}}),{times:1})
  await input.fill(question);await p.getByRole('button',{name:'发送',exact:true}).click()
  await p.getByText(/资料已更新/).first().waitFor()
})
test('read race does not replace the newly selected fixture',async t=>{
  const p=await open(t,1440)
  let mark,release
  const started=new Promise(resolve=>mark=resolve), held=new Promise(resolve=>release=resolve)
  await p.route('**/fixtures/'+FOREST+'.json',async r=>{const res=await r.fetch();mark();await held;await r.fulfill({response:res}).catch(()=>{})})
  await p.goto(pages)
  await row(p,FOREST).click()
  await started
  try {
    await row(p,ARSENAL).click()
    await p.getByRole('heading',{name:'胜平负预测'}).waitFor()
  }finally{release()}
  await p.waitForLoadState('networkidle')
  assert.ok(p.url().includes(ARSENAL))
  assert.match(await p.locator('article').last().innerText(),/阿森纳/)
})

test('a narrow draw lead, a tie, and a same-time market comparison use the displayed precision',async t=>{
  const p=await open(t,360)
  let chances=[.33449,.33451,.331]
  await p.route('**/fixtures/'+FOREST+'.json',async route=>{
    const game=await (await route.fetch()).json()
    Object.assign(game.prediction,{a:chances,b:[.45,.3,.25],market:[.35,.35,.3]})
    await route.fulfill({json:game})
  })
  await p.goto(pages+'#/match/'+FOREST)
  await p.locator('.fan-judgment').filter({hasText:/平局比.*高 0.1 个百分点/}).waitFor()
  await p.getByText('市场怎么看',{exact:true}).click()
  await p.locator('.fan-market').getByText(/融合模型 B.*主胜.*10.0 个百分点/).waitFor()
  chances=[.38,.24,.38]
  await p.reload()
  await p.locator('.fan-judgment').filter({hasText:/并列/}).waitFor()
})

test('a disputed score, unconfirmed time and late result each have a distinct view',async t=>{
  const variants=[
    [{source_conflict:true,hg:2,ag:1},'比分待核实'],
    [{schedule_status:'conflict',kickoff_utc:null},'开球时间待定'],
    [{kickoff_utc:'2026-11-25T20:00:00Z',schedule_status:'verified'},'等待赛果']
  ]
  for(const [fields,status] of variants){
    const p=await open(t,360)
    await p.route('**/board.json',async route=>{
      const data=await (await route.fetch()).json()
      Object.assign(data.fixtures.find(f=>f.id===FOREST),fields)
      await route.fulfill({json:data})
    })
    await p.route('**/fixtures/'+FOREST+'.json',async route=>{
      const game=await (await route.fetch()).json()
      await route.fulfill({json:{...game,...fields}})
    })
    await p.goto(pages)
    await row(p,FOREST).getByText(status,{exact:true}).waitFor()
    await row(p,FOREST).click()
    const detail=p.getByRole('article',{name:'比赛详情'})
    await detail.getByText(status,{exact:true}).waitFor()
    assert.equal(await detail.locator('.fan-odds, .fan-score').count(),0)
  }
})

test('research permalink retains the witness, frozen download and single-game score',async t=>{
  const p=await open(t,1440)
  const witness='https://github.com/example/repo/issues/1#issuecomment-123'
  const snapshot='https://raw.githubusercontent.com/example/repo/'+'a'.repeat(40)+'/snapshot.json'
  await p.route('**/board.json',async route=>{
    const data=await (await route.fetch()).json()
    await route.fulfill({json:{...data,public_snapshot:true,fixed_snapshot_url:snapshot}})
  })
  await p.route('**/report.json',async route=>{
    const report=await (await route.fetch()).json()
    const base={id:PAST,season:'2026-27',home:'Fulham',away:'Manchester United',kickoff:'2026-08-14T19:00:00Z'}
    const paired={...base,status:'paired',reason:'paired',actual:'D',a:[.4,.35,.25],b:[.32,.39,.29],market:[.3,.4,.3],evidence:witness}
    const excluded={...base,id:'test-conflict',reason:'source_conflict',status:'excluded'}
    await route.fulfill({json:{...report,pilot:{n_pairs:1,seasons:[],rows:[paired,excluded]}}})
  })
  await p.goto(pages+'#/report')
  const receipt=p.locator('section').filter({has:p.getByRole('heading',{name:'首场公开核验'})})
  assert.equal(await receipt.getByRole('link',{name:/本场赛前公开见证/}).getAttribute('href'),witness)
  assert.match(await receipt.locator('tbody tr').first().innerText(),/1\.0498/)
  assert.equal(await receipt.getByRole('link',{name:/预测快照 JSON/}).getAttribute('href'),snapshot)
  await p.getByLabel('记录来源').selectOption('pilot')
  await p.getByLabel('缺失原因或状态').selectOption('source_conflict')
  assert.match(await p.locator('.rp-record-table tbody tr').innerText(),/比分待核实/)
  await receipt.getByRole('button').click()
  await p.getByRole('article',{name:'比赛详情'}).getByText('已完场',{exact:true}).waitFor()
})

test('a finished match can be traced and downloaded from its own source link',async t=>{
  for(const url of [live,pages]){
    const p=await open(t,360,url+'#/match/'+PAST)
    await p.getByRole('link',{name:/本场数据来源与方法/}).click()
    await p.getByRole('heading',{name:/富勒姆 对 曼联/}).waitFor()
    const download=p.waitForEvent('download')
    await p.getByRole('link',{name:'下载本场记录 JSON'}).click()
    const record=JSON.parse(await readFile(await (await download).path(),'utf8'))
    assert.equal(record.id,PAST)
    assert.deepEqual([record.hg,record.ag],[1,1])
    await p.getByRole('link',{name:/返回本场比赛/}).click()
    await p.getByRole('article',{name:'比赛详情'}).getByText('已完场',{exact:true}).waitFor()
  }
})
if(Number(process.env.EPL_PERF_SAMPLES)>=10&&engine===chromium)test('mobile matchweek and first actual probability summary on a cold connection',async t=>{
  const rounds=[],summaries=[],count=Number(process.env.EPL_PERF_SAMPLES)
  for(let i=0;i<count;i++){
    const p=await browser.newPage({viewport:{width:360,height:800},isMobile:true,hasTouch:true,locale:'zh-CN',timezoneId:'Asia/Shanghai'})
    try {
      await p.clock.setFixedTime(new Date(STAMP))
      const cdp=await p.context().newCDPSession(p)
      await cdp.send('Network.enable')
      await cdp.send('Network.setCacheDisabled',{cacheDisabled:true})
      await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:120,downloadThroughput:200000,uploadThroughput:90000})
      await cdp.send('Emulation.setCPUThrottlingRate',{rate:4})
      await p.goto(pages,{waitUntil:'domcontentloaded'})
      await p.getByTestId('fixture-row').nth(1).waitFor()
      rounds.push(Math.round(await p.evaluate(()=>performance.now())))
      await p.getByTestId('forecast-summary').first().waitFor()
      summaries.push(Math.round(await p.evaluate(()=>performance.now())))
    }finally{await p.close()}
  }
  const p95=xs=>[...xs].sort((a,b)=>a-b)[Math.ceil(xs.length*.95)-1]
  t.diagnostic(JSON.stringify({count,rounds,summaries,roundP95:p95(rounds),summaryP95:p95(summaries)}))
  assert(p95(rounds)<=2500 && p95(summaries)<=2500,'mobile cold-start budget exceeded')
})

test('report filters a real season, separates draw calibration and downloads the selected matches',async t=>{
  const page=await open(t,768,pages+'#/report')
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
  await page.getByRole('article',{name:'比赛详情'}).getByText('已完场',{exact:true}).waitFor()
  await fits(page,768)
})
