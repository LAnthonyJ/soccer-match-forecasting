// Executed on the enrolled probe itself; no mocked URLs, clock or network.
const {chromium}=require('../frontend/node_modules/playwright');
const {readFile}=require('node:fs/promises');
async function main() {
  const [site,id]=process.argv.slice(2);
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({locale:'zh-CN',timezoneId:'Asia/Shanghai',viewport:{width:390,height:844}});
    page.setDefaultTimeout(15000);
    await page.goto(site+'/#/match/'+encodeURIComponent(id),{waitUntil:'domcontentloaded'});
    await page.getByRole('heading',{name:'胜平负预测'}).waitFor();
    await page.getByRole('button',{name:/阅读 AI 赛事分析/}).click();
    await page.getByRole('link',{name:'核对本场分析依据'}).waitFor();
    await page.getByRole('link',{name:'模型成绩'}).click();
    const download=page.waitForEvent('download');
    await page.getByRole('button',{name:/下载当前筛选 CSV/}).click();
    const file=await download;
    if (await file.failure()) throw new Error('download failed');
    const csv=await readFile(await file.path(),'utf8');
    if (!csv.trim().includes('\n') || csv.trim().startsWith('<')) throw new Error('invalid record download');
    console.log('Selected a match, inspected its evidence and downloaded the scorecard.');
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
