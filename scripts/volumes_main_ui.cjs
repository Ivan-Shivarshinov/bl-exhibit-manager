const {chromium}=require('../web/node_modules/playwright');
const enterApp=require('./local_browser_access.cjs');
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
(async()=>{
 const base=process.env.VOLUME_QA_URL,out=process.env.VOLUME_QA_OUTPUT;
 const browser=await chromium.launch();const errors=[];
 try{
  const page=await browser.newPage({viewport:{width:1400,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));await enterApp(page,base);
  await page.getByLabel('Проект',{exact:true}).selectOption(process.env.VOLUME_QA_PID);
  await page.getByRole('button',{name:'Проверка и экспорт',exact:true}).click();
  const panel=page.locator('.volume-section'),prepare=panel.getByRole('button',{name:'Подготовить PDF-тома',exact:true});
  await prepare.click();await panel.getByRole('heading',{name:'Состав и страницы томов'}).waitFor({timeout:60000});
  const first=panel.locator('.volume-preview details label').filter({hasText:'Сноска 1'}).first();
  await first.locator('input').fill('1');await first.getByRole('button',{name:'Подтвердить страницу',exact:true}).click();
  assert(await panel.getByRole('button',{name:'Скачать ZIP с PDF-томами',exact:true}).isDisabled());
  await panel.getByRole('button',{name:'Сохранить настройки томов',exact:true}).click();
  await prepare.click();await panel.getByRole('heading',{name:'Состав и страницы томов'}).waitFor({timeout:60000});
  await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Скачать ZIP с PDF-томами')?.disabled);
  await panel.getByText('Карта исходных и подготовленных страниц',{exact:true}).click();
  assert(await panel.locator('.volume-preview details').first().locator('tbody tr').count()>=5);
  await page.screenshot({path:path.join(out,'main-page-choices.png'),fullPage:true});
  const download=page.waitForEvent('download');await panel.getByRole('button',{name:'Скачать ZIP с PDF-томами',exact:true}).click();
  await(await download).saveAs(path.join(out,'UI-main-volumes.zip'));
  assert.equal(errors.length,0,errors.join('\n'));
  fs.writeFileSync(path.join(out,'main-ui-report.json'),JSON.stringify({passed:true,checks:['real converter','exact page edited and confirmed','stale export blocked','source map visible','ZIP download'],pageErrors:errors},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
