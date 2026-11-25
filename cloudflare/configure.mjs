import {readFile,writeFile,copyFile,access} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';

export async function configure(env) {
  const name=env.EPL_CF_PAGES_PROJECT, db=env.CLOUDFLARE_D1_DATABASE_ID, tariff=env.EPL_PRICING_VALID_UNTIL??'';
  if (typeof name!=='string' || !/^[a-z0-9](?:[a-z0-9-]{0,56}[a-z0-9])?$/.test(name)) throw new Error('Invalid EPL_CF_PAGES_PROJECT');
  if (typeof db!=='string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(db)) throw new Error('Invalid CLOUDFLARE_D1_DATABASE_ID');
  if (tariff && (!/^\d{4}-\d{2}-\d{2}$/.test(tariff) || !Number.isFinite(Date.parse(tariff)) ||
      new Date(tariff).toISOString().slice(0,10)!==tariff)) throw new Error('Invalid EPL_PRICING_VALID_UNTIL');
  const template=await readFile(new URL('./wrangler.toml.example',import.meta.url),'utf8');
  return template.replace('name = "epl-matchdesk"',`name = "${name}"`)
    .replace('REPLACE-WITH-YOUR-D1-UUID',db).replace('PRICING_VALID_UNTIL = ""',`PRICING_VALID_UNTIL = "${tariff}"`);
}
if (resolve(process.argv[1]??'')===fileURLToPath(import.meta.url)) {
  try {
    const config=await configure(process.env);
    await access(new URL('./site/index.html',import.meta.url));
    await copyFile(new URL('./_routes.json',import.meta.url),new URL('./site/_routes.json',import.meta.url));
    await writeFile(new URL('./wrangler.toml',import.meta.url),config);
    console.log('Configured the Pages project, D1 binding and site routes.');
  } catch(e) {console.error(e.message);process.exitCode=1;}
}
