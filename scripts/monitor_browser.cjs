// Executed on the enrolled probe itself; no mocked URLs, clock or network.
const {chromium}=require('../frontend/node_modules/playwright');
const {readFile}=require('node:fs/promises');
async function main() {
  const [site,id,factHash,expectedAsn]=process.argv.slice(2);
  const result={completed:false,ai:'failed'};
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({locale:'zh-CN',timezoneId:'Asia/Shanghai',viewport:{width:390,height:844}});
    page.setDefaultTimeout(15000);
    await page.goto(site+'/#/match/'+encodeURIComponent(id),{waitUntil:'domcontentloaded'});
    const network=await page.evaluate(async()=>{
      const res=await fetch('/api/monitor/network',{cache:'no-store'});
      return res.ok?res.json():null;
    });
    if (network?.country!=='CN' || network.asn!==Number(expectedAsn)) throw new Error('Browser egress changed');
    await page.getByRole('heading',{name:'胜平负预测'}).waitFor();
    await page.getByRole('button',{name:'问这场',exact:true}).click();
    await page.getByLabel('关于这场比赛的提问').fill('这场的赛前概率和主要依据是什么？');
    const [reply]=await Promise.all([
      page.waitForResponse(r=>r.url()===site+'/api/analysis/ask' && r.request().method()==='POST'),
      page.getByRole('button',{name:'发送',exact:true}).click()
    ]);
    if (reply.ok()) {
      const body=await reply.json();
      if (body.fixture_id===id && body.fact_hash===factHash && body.view==='prematch' && body.answer) {
        await page.locator('.ai-turn p').filter({hasText:body.answer.split('\n')[0]}).first().waitFor();
        result.ai=body.source==='deepseek' && body.model==='deepseek-flash' && body.sections?.length?'deepseek':'fallback';
      }
    }
    await page.getByRole('button',{name:'展开分析',exact:true}).click();
    await page.getByRole('link',{name:'核对本场分析依据'}).click();
    await page.getByRole('status').filter({hasText:factHash}).waitFor();
    await page.getByRole('link',{name:'返回比赛看板'}).click();
    await page.getByRole('link',{name:/数据来源与方法/}).last().click();
    await page.getByText('研究成绩与公开记录',{exact:true}).click();
    await page.getByRole('link',{name:'查看模型成绩及复算下载'}).click();
    await page.getByLabel('记录来源').selectOption('history');
    // A season download exercises the actual filtered CSV without fetching the whole archive.
    const season=page.getByLabel('记录赛季');
    await season.locator('option').nth(1).waitFor({state:'attached'});
    await season.selectOption({index:1});
    const download=page.waitForEvent('download');
    await page.getByRole('button',{name:/下载当前筛选 CSV/}).click();
    const file=await download;
    if (await file.failure()) throw new Error('download failed');
    const csv=await readFile(await file.path(),'utf8');
    if (!csv.trim().includes('\n') || csv.trim().startsWith('<')) throw new Error('invalid record download');
    result.completed=true;
  } catch { /* The monitoring record stores state, never a response body or a screenshot. */ }
  finally {await browser.close();}
  console.log(JSON.stringify(result));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
