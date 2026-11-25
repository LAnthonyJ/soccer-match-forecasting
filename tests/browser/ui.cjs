// Run from frontend with npm run test:browser. Both entry points use the shipped EPL data.
const { before, after, test } = require('node:test')
const assert = require('node:assert/strict')
const { createRequire } = require('node:module')
const { spawn, execFileSync } = require('node:child_process')
const { createServer } = require('node:http')
const { mkdtemp, readFile, rm, mkdir } = require('node:fs/promises')
const { tmpdir } = require('node:os')
const path = require('node:path')
const readline = require('node:readline')
const root = path.resolve(__dirname, '../..')
const { chromium } = createRequire(path.join(root, 'frontend/package.json'))('playwright')
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
      res.writeHead(200,{'content-type':(mime[path.extname(file)]||'application/octet-stream')+';charset=utf-8'}).end(body)
    }catch{res.writeHead(404).end()}
  })
  await new Promise(resolve=>staticServer.listen(0,'127.0.0.1',resolve))
  pages='http://127.0.0.1:'+staticServer.address().port+'/demo/'
  browser=await chromium.launch({headless:true,executablePath:process.env.EPL_BROWSER_PATH||undefined})
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
  const page=await browser.newPage({viewport:{width,height:width<650?840:960},locale:'zh-CN',timezoneId:'Asia/Shanghai'})
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
  await page.route('**/api/dashboard',route=>{
    if(first){first=false;return route.abort('failed')}
    return route.continue()
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
  await page.route('**/dashboard.json',async route=>{
    const response=await route.fetch(), data=await response.json()
    const m=data.fixtures.find(f=>f.prediction&&f.hg!==null)
    const base={id:m.id,season:m.season,home:m.home,away:m.away,kickoff:m.kickoff_utc}
    const paired={...base,status:'paired',reason:'paired',actual:'D',a:[.4,.35,.25],b:[.32,.39,.29],market:[.3,.4,.3],evidence:'https://github.com/test/test/issues/1#issuecomment-123'}
    data.report.pilot={n_pairs:1,seasons:[],rows:[paired,{...base,id:'test-conflict',reason:'source_conflict',status:'excluded'}]}
    data.public_snapshot=true
    await route.fulfill({response,json:data})
  })
  await page.reload()
  await page.getByRole('link',{name:'模型成绩'}).click()
  const proof=page.locator('section').filter({has:page.getByRole('heading',{name:'首场公开核验'})})
  assert.equal(await proof.getByRole('link',{name:/本场赛前公开见证/}).getAttribute('href'),'https://github.com/test/test/issues/1#issuecomment-123')
  assert.match(await proof.locator('tbody tr').first().innerText(),/1\.0498/)
  assert.equal(await proof.getByRole('link',{name:/预测快照 JSON/}).getAttribute('href'),'./snapshot.json')
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
    await page.route('**/dashboard.json',async route=>{
      const response=await route.fetch(), data=await response.json()
      const game=data.fixtures.find(m=>m.id===data.default_id)
      fixtureId=game.id
      game.trace={revision:2,schedule_history:[],score:{log_loss:0.9163,brier:0.545,correct:true}}
      game.market_note='采集机未配置赔率密钥'
      data.report.pilot={n_pairs:0,seasons:[],independent:{n_scored:1,total:380,eligible:310,finished:1,
        coverage:1/310,finished_coverage:1,scores:{accuracy:1,log_loss:.9163,brier:.545},missing:{},
        rows:[{id:game.id,season:game.season,home:game.home,away:game.away,kickoff:game.kickoff_utc,
          actual:'H',a:[.4,.35,.25],status:'scored',reason:'scored',evidence:'https://github.com/example/repo/issues/1#issuecomment-1'}]}}
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
    await page.getByText('缺少市场报价',{exact:true}).click()
    await page.getByText('采集机未配置赔率密钥',{exact:true}).first().waitFor()
    await layout(page,width)
  })
}
