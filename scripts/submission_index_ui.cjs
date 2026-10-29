// Browser plugin not available: real Chromium against an isolated synthetic server.
const {chromium}=require('../web/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.INDEX_QA_URL,output=process.env.INDEX_QA_OUTPUT;
(async()=>{
 const browser=await chromium.launch(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!(m.location().url.endsWith('/submission-index/preview')&&m.text().includes('400')))errors.push(m.text());});
  await page.goto(base);assert.match(await page.title(),/Buzko Legal/);
  if(process.env.INDEX_QA_PID){
   await page.getByLabel('Проект',{exact:true}).selectOption(process.env.INDEX_QA_PID);
  }
  await page.getByRole('button',{name:'Проверка и экспорт',exact:true}).click();
  const panel=page.locator('.submission-index');await panel.waitFor();
  const refresh=panel.getByRole('button',{name:'Обновить перечень',exact:true});
  let previews=0;
  await page.route('**/submission-index/preview',async route=>{previews++;await new Promise(done=>setTimeout(done,250));await route.continue();});
  await page.evaluate(()=>{const b=[...document.querySelectorAll('.submission-index button')].find(b=>b.textContent==='Обновить перечень');b.click();b.click();});
  await panel.getByRole('status').filter({hasText:'Перечень проверен: 100'}).waitFor();
  assert.equal(previews,1,'Double click must create one preview');
  assert.equal(await panel.locator('tbody tr').count(),100);
  const custom='Test index — custom title';await panel.getByLabel('Заголовок перечня').fill(custom);
  await panel.getByLabel('Язык подписей').selectOption('en');assert.equal(await panel.getByLabel('Заголовок перечня').inputValue(),custom);
  assert(await refresh.isDisabled());await panel.getByRole('button',{name:'Сохранить перечень',exact:true}).click();await refresh.waitFor({state:'visible'});
  // Wait for save to finish (the refresh button becomes enabled).
  await page.waitForFunction(()=>[...document.querySelectorAll('.submission-index button')].find(b=>b.textContent==='Обновить перечень')?.disabled===false);
  await refresh.click();await panel.getByRole('status').filter({hasText:'Перечень проверен: 100'}).waitFor();
  const checkbox=panel.getByLabel('Добавить перечень приложений',{exact:true});
  await checkbox.focus();await page.keyboard.press('Space');assert.equal(await checkbox.isChecked(),false);
  await page.keyboard.press('Space');assert.equal(await checkbox.isChecked(),true);
  await panel.getByLabel('Заголовок перечня').focus();await page.keyboard.press('Tab');
  assert(await panel.getByLabel('Язык подписей').evaluate(el=>el===document.activeElement));
  const table=panel.getByRole('region',{name:'Предпросмотр перечня приложений'});
  await table.focus();await page.keyboard.press('ArrowDown');
  await page.waitForFunction(()=>document.querySelector('.index-table-scroll').scrollTop>0);
  // Another client changes the saved project while this window retains its preview.
  const headers={'X-Exhibit-Local':'1'};
  const projects=await (await page.request.get(base+'/api/projects')).json();
  const current=await (await page.request.get(base+'/api/projects/'+(process.env.INDEX_QA_PID||projects[0].id))).json();
  const changed=await page.request.post(`${base}/api/projects/${current.id}/documents/${current.documents[0].id}`,{headers,data:{title:'Изменено в другом окне — synthetic'}});
  assert(changed.ok());
  await panel.getByRole('status').filter({hasText:'Перечень нужно обновить.'}).waitFor({timeout:10000});
  assert(await page.getByRole('button',{name:'Собрать подачу · ZIP',exact:true}).isDisabled());
  await refresh.click();await panel.getByRole('status').filter({hasText:'Перечень проверен: 100'}).waitFor();
  await page.route('**/submission-index/preview',async route=>{await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({detail:'Синтетический отказ проверки'})});},{times:1});
  await refresh.click();await panel.getByRole('alert').filter({hasText:'Синтетический отказ проверки'}).waitFor();
  await refresh.click();await panel.getByRole('status').filter({hasText:'Перечень проверен: 100'}).waitFor();
  for(const width of [1440,760,390]){
   await page.setViewportSize({width,height:1000});
   assert(await panel.getByLabel('Заголовок перечня').isVisible());
   assert.equal(await page.locator('vite-error-overlay').count(),0);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:path.join(output,`ui-${width}.png`),fullPage:true});
  }
  await page.setViewportSize({width:1440,height:1000});
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Собрать подачу · ZIP',exact:true}).click();
  await (await download).saveAs(path.join(output,'UI-Submission.zip'));
  await page.reload();await page.getByRole('button',{name:'Проверка и экспорт',exact:true}).click();
  assert.equal(await panel.getByLabel('Заголовок перечня').inputValue(),custom);
  await page.keyboard.press('Tab');assert(await page.evaluate(()=>document.activeElement!==document.body));
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'ui-report.json'),JSON.stringify({count:100,widths:[1440,760,390],customTitlePersisted:true,doubleClickGuard:true,keyboard:true,otherWindowStale:true,errorAndRetry:true,errors},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
