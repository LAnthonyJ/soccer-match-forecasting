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
  const items=page.locator('button.fixture')
  const one=await items.nth(0).innerText(),two=await items.nth(1).innerText()
  assert.notEqual(one,two)
  let release
  const held=new Promise(resolve=>{release=resolve})
  let count=0
  await page.route('**/api/fixtures/*',async route=>{
    if(++count===1)await held
    await route.continue().catch(()=>{})
  })
  const first=page.waitForRequest('**/api/fixtures/*')
  await items.nth(0).click()
  await first
  await items.nth(1).click()
  const next=await page.locator('.matchup').innerText()
  release()
  await page.waitForLoadState('networkidle')
  assert.equal(await page.locator('.matchup').innerText(),next)
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
