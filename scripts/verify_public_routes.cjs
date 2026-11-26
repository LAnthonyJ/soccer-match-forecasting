// Anonymous release check against the deployed Pages application and its pinned snapshot.
const assert = require('node:assert/strict');
const {readFile} = require('node:fs/promises');
const {createHash} = require('node:crypto');
const path = require('node:path');
const {chromium} = require('../frontend/node_modules/playwright');

async function main() {
  const root=path.resolve(__dirname,'..');
  const site=(process.argv[2]||'https://lanthonyj.github.io/worldcup_prediction').replace(/\/$/,'');
  const commit=process.argv[3]||(await readFile(path.join(root,'.github/epl-public-data-sha'),'utf8')).trim();
  assert.match(commit,/^[0-9a-f]{40}$/);
  const browser=await chromium.launch({headless:true,executablePath:process.env.EPL_BROWSER_PATH||undefined});
  try {
    const ctx=await browser.newContext({locale:'zh-CN',timezoneId:'Asia/Shanghai',viewport:{width:390,height:844}});
    const page=await ctx.newPage();
    page.setDefaultTimeout(20000);
    const read=async file=>{
      const res=await ctx.request.get(site+'/'+file,{failOnStatusCode:true,timeout:30000});
      return res.body();
    };
    const marker=async()=>assert.equal((await read('public-data-commit.txt')).toString().trim(),commit,'public batch commit');
    await marker();
    const board=JSON.parse(await read('board.json'));
    const bytes=await read('snapshot.json');
    assert.equal(createHash('sha256').update(bytes).digest('hex'),board.batch_id,'download SHA-256');
    const batch=JSON.parse(bytes).data;
    const routes={home:site+'/',legacy:site+'/#/ask'};
    await page.goto(routes.home,{waitUntil:'domcontentloaded'});
    await page.getByRole('heading',{name:'英超赛程',exact:true}).waitFor();
    const rows=page.getByTestId('fixture-row');
    await rows.first().waitFor();
    const ids=await rows.evaluateAll(elements=>elements.map(e=>e.dataset.fixtureId).sort());
    assert.deepEqual(ids,board.fixtures.map(f=>f.id).sort(),'all fixtures rendered');
    const forecast=board.fixtures.find(f=>f.id===board.current_id && f.prediction && Date.parse(f.kickoff_utc)>Date.now())||
      board.fixtures.find(f=>f.prediction && Date.parse(f.kickoff_utc)>Date.now());
    assert.ok(forecast,'a future match with published probabilities is required for the prematch acceptance');
    const final=batch.fixtures.filter(f=>f.season===board.current_season && f.hg!=null && f.ag!=null &&
      !f.source_conflict && !f.withdrawn).sort((a,b)=>b.date.localeCompare(a.date))[0];
    assert.ok(final,'a settled match is required for the score permalink acceptance');
    routes.prematch=site+'/#/match/'+forecast.id;
    routes.final=site+'/#/match/'+final.id;
    await page.goto(routes.legacy);
    await page.getByRole('button',{name:'问这场',exact:true}).click();
    await page.getByLabel('关于这场比赛的提问').waitFor();
    await page.goto(routes.prematch);
    const article=page.getByRole('article',{name:'比赛详情'});
    await article.getByRole('heading',{name:'胜平负预测'}).waitFor();
    assert.deepEqual((await article.locator('.fan-odds strong').allTextContents()).map(t=>t.trim()),
      forecast.prediction.a.map(v=>(v*100).toFixed(1)+'%'),'rendered home/draw/away probabilities');
    const ask=article.getByRole('button',{name:'问这场',exact:true});
    if(await ask.getAttribute('aria-expanded')!=='true')await ask.click();
    const prompt=article.locator('.ai-prompts button').first();
    await prompt.click();
    const answer=article.locator('.ai-turn').first();
    await answer.waitFor();
    assert.equal(await answer.getAttribute('data-answer-fixture-id'),forecast.id);
    assert.ok(await answer.getAttribute('data-as-of'));
    assert.ok((await answer.locator('p').first().innerText()).trim());
    await page.goto(routes.final);
    await article.getByText('已完场',{exact:true}).waitFor();
    await article.getByLabel('比分 '+final.hg+' 比 '+final.ag).waitFor();
    assert.equal(await article.locator('.fan-odds').count(),0);
    await marker();
    console.log(JSON.stringify({verified:true,checked_at:new Date().toISOString(),commit,snapshot_sha256:board.batch_id,
      routes,round_ids:ids,forecast:{id:forecast.id,a:forecast.prediction.a},final:{id:final.id,score:[final.hg,final.ag]}},null,2));
  } finally {
    await browser.close();
  }
}
main().catch(err=>{console.error(err.message);process.exitCode=1;});
