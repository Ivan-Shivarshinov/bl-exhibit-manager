// Official browser download flow, confined to disposable GitHub runners.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
if (process.env.GITHUB_ACTIONS !== 'true' || process.env.RUNNER_ENVIRONMENT !== 'github-hosted') {
  throw new Error('Only for disposable hosted CI, never a user workstation.');
}
const {chromium} = require('../web/node_modules/playwright');
(async () => {
  const output = path.resolve(process.argv[2]);
  fs.mkdirSync(path.dirname(output), {recursive:true});
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await page.goto('https://www.sumatrapdfreader.org/downloadafter');
    const [download] = await Promise.all([
      page.waitForEvent('download', {timeout:90000}),
      page.getByRole('link', {name:'SumatraPDF-3.6.1-64.zip', exact:true}).click()
    ]);
    await download.saveAs(output);
    const sha = crypto.createHash('sha256').update(fs.readFileSync(output)).digest('hex');
    if (sha !== '98b33a518d42986856d225064b0cd2d3643ecf78cbf84ab873d26cc51877a544') {
      throw new Error('Official viewer ZIP checksum mismatch');
    }
    console.log(JSON.stringify({file:output,sha256:sha,version:'3.6.1'}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
