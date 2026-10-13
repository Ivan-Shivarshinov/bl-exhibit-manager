// Real browser interactions against an isolated profile and synthetic fixtures.
const {chromium}=require('../web/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const base=process.env.READY_QA_URL,dir=process.env.READY_QA_OUTPUT;
assert(base&&dir,'Set isolated READY_QA_URL and READY_QA_OUTPUT');
(async()=>{
 const browser=await chromium.launch({...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',msg=>{if(msg.type()==='error')errors.push(msg.text());});
  const request=async(route,body)=>{
   const response=await page.request.fetch(base+'/api'+route,{method:body===undefined?'GET':'POST',headers:{'X-Exhibit-Local':'1'},...(body===undefined?{}:{data:body})});
   assert(response.ok(),`${route}: ${await response.text()}`);return response.json();
  };
  await page.goto(base);await page.getByLabel('Проект',{exact:true}).locator('option').first().waitFor({state:'attached'});
  assert.match(await page.locator('.brand').textContent(),/Buzko Legal/);
  assert.equal(await page.locator('vite-error-overlay').count(),0);
  const seeded=JSON.parse(fs.readFileSync(path.join(dir,'ready-api-report.json'))),pid=seeded.pid;
  await page.getByLabel('Проект',{exact:true}).selectOption(pid);
  await page.getByRole('button',{name:'Ссылки в Word',exact:true}).click();
  const before=await request('/projects/'+pid),ids=before.documents.map(d=>d.id);
  const selects=page.locator('.links-table select');await selects.first().waitFor();
  const values=await selects.first().locator('option').evaluateAll(options=>options.map(o=>({value:o.value,text:o.textContent})));
  assert.equal(values[0].value,'');assert.equal(values[1].value,'__keep_original__');
  const numbered=values.slice(2).filter(o=>/^Annex \d+ ·/.test(o.text));
  assert.deepEqual(numbered.map(o=>Number(o.text.match(/^Annex (\d+)/)[1])),Array.from({length:105},(_,i)=>i+1));
  assert.match(values.at(-2).text,/Title 2/);assert.match(values.at(-1).text,/Title 10/);
  const target=before.documents.find(d=>d.number===2).id;
  await page.getByLabel('Применять ручной выбор к одинаковым упоминаниям').check();
  await selects.first().selectOption(target);
  await page.waitForFunction(t=>[...document.querySelectorAll('.links-table select')].slice(0,2).every(s=>s.value===t&&!s.disabled),target);
  await page.reload();await page.getByRole('button',{name:'Ссылки в Word',exact:true}).click();
  await page.getByRole('button',{name:'Сопоставить ссылки',exact:true}).click();
  await page.waitForFunction(t=>[...document.querySelectorAll('.links-table select')].slice(0,2).every(s=>s.value===t&&!s.disabled),target);
  await selects.nth(2).selectOption('__keep_original__');
  await page.waitForFunction(()=>document.querySelectorAll('.links-table select')[2]?.value==='__keep_original__');
  await page.getByRole('button',{name:'Добавить ссылку',exact:true}).first().click();
  const dialog=page.getByRole('dialog',{name:'Добавить ссылку'});
  await dialog.locator('select').first().selectOption('3');
  await dialog.getByLabel('Текст новой ссылки').fill('Manual title without identifier');
  const manualValues=await dialog.getByLabel('Файл новой ссылки').locator('option').evaluateAll(options=>options.map(o=>o.value));
  assert.deepEqual(manualValues.slice(1),values.slice(2).map(o=>o.value));
  const manualTarget=before.documents.find(d=>d.number===9).id;
  await dialog.getByLabel('Файл новой ссылки').selectOption(manualTarget);
  await dialog.getByRole('button',{name:'Создать ссылку',exact:true}).click();await dialog.waitFor({state:'hidden'});
  const after=await request('/projects/'+pid);assert.deepEqual(after.documents.map(d=>d.id),ids);
  assert(after.references.some(r=>r.target===manualTarget&&r.mention==='Manual title without identifier'));
  for(const width of [1440,760,390]) {
   await page.setViewportSize({width,height:1000});await page.screenshot({path:path.join(dir,`reference-order-${width}.png`)});
  }
  // Entire ready-file upload/export is performed through visible controls.
  await page.setViewportSize({width:1440,height:1000});await page.locator('header').getByRole('button',{name:'Новая',exact:true}).click();
  await page.getByLabel('Название',{exact:true}).fill('UI ready signed PDF');await page.getByRole('button',{name:'Создать',exact:true}).click();
  await page.locator('.modal-backdrop').waitFor({state:'hidden'});
  await page.getByLabel('Добавить готовые PDF',{exact:true}).setInputFiles(path.join(dir,'fixtures','Annex 65.pdf'));
  await page.locator('.row-button').waitFor();await page.getByRole('button',{name:'Результат',exact:true}).click();
  await page.locator('.preview img').waitFor();await page.getByLabel('Страница предпросмотра').fill('2');
  await page.waitForFunction(()=>{const img=document.querySelector('.preview img');return img?.complete&&img.naturalWidth>0;});
  await page.screenshot({path:path.join(dir,'ready-field-preview.png')});
  await page.getByRole('button',{name:/Проверка и экспорт/}).click();
  const downloading=page.waitForEvent('download');await page.getByRole('button',{name:'Собрать подачу · ZIP',exact:true}).click();
  await(await downloading).saveAs(path.join(dir,'ui-ready.zip'));
  let libraryUi=false;
  await page.getByRole('button',{name:'Документы',exact:true}).click();
  if(await page.getByRole('button',{name:'Сохранить в библиотеку',exact:true}).count()) {
   const sourcePid=await page.getByLabel('Проект',{exact:true}).inputValue(),sourceBefore=await request('/projects/'+sourcePid);
   await page.getByRole('button',{name:'Сохранить в библиотеку',exact:true}).click();
   const dlg=page.locator('.library-dialog'),name='UI ready fields '+Date.now();
   await dlg.getByLabel('Название новой библиотеки').fill(name);
   // Do not spend the response deadline waiting for an unrelated catalog load
   // to re-enable the form. This is an action readiness check, not a retry.
   const events=[];const onRequest=r=>{if(new URL(r.url()).pathname.startsWith('/api/libraries'))events.push({event:'request',method:r.method(),path:new URL(r.url()).pathname});};
   const onResponse=r=>{if(new URL(r.url()).pathname.startsWith('/api/libraries'))events.push({event:'response',status:r.status(),path:new URL(r.url()).pathname});};
   const onFailed=r=>{if(new URL(r.url()).pathname.startsWith('/api/libraries'))events.push({event:'failed',path:new URL(r.url()).pathname,failure:r.failure()});};
   page.on('request',onRequest);page.on('response',onResponse);page.on('requestfailed',onFailed);
   let createdLibrary;
   try {
    await page.waitForFunction(expected=>{const form=document.querySelector('.library-dialog form');return form?.querySelector('input')?.value===expected&&!form.querySelector('button')?.disabled;},name);
    const [createdResponse]=await Promise.all([
     page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/libraries'),
     dlg.getByRole('button',{name:'Создать библиотеку',exact:true}).click(),
    ]);assert(createdResponse.ok(),await createdResponse.text());
    createdLibrary=await createdResponse.json();assert.equal(createdLibrary.name,name);
    await page.waitForFunction(id=>document.querySelector('.library-dialog select[aria-label="Библиотека"]')?.value===id,createdLibrary.id);
   } catch(e){await page.screenshot({path:path.join(dir,'library-create-failure.png')});fs.writeFileSync(path.join(dir,'library-create-failure.json'),JSON.stringify({created:createdLibrary,events,input:await dlg.getByLabel('Название новой библиотеки').inputValue(),disabled:await dlg.getByRole('button',{name:'Создать библиотеку',exact:true}).isDisabled(),validity:await dlg.getByLabel('Название новой библиотеки').evaluate(el=>({valid:el.validity.valid,message:el.validationMessage})),options:await dlg.getByLabel('Библиотека',{exact:true}).locator('option').allTextContents(),value:await dlg.getByLabel('Библиотека',{exact:true}).inputValue(),errors:await dlg.locator('[role="alert"]').allTextContents()},null,2));throw e;}
   finally {page.off('request',onRequest);page.off('response',onResponse);page.off('requestfailed',onFailed);}
   const lid=await dlg.getByLabel('Библиотека',{exact:true}).inputValue();
   await dlg.getByRole('button',{name:'Проверить состав',exact:true}).click();
   await dlg.getByRole('button',{name:'Сохранить материалы',exact:true}).click();
   await dlg.getByText('Решения применены. Исходная подача не изменена.',{exact:true}).waitFor();
   await dlg.getByRole('button',{name:'Вернуться к подаче',exact:true}).click();
   await page.locator('header').getByRole('button',{name:'Новая',exact:true}).click();
   await page.getByLabel('Название',{exact:true}).fill('UI independent form library');await page.getByRole('button',{name:'Создать',exact:true}).click();
   await page.locator('.modal-backdrop').waitFor({state:'hidden'});
   await page.getByRole('button',{name:'Добавить из библиотеки',exact:true}).click();
   await dlg.getByLabel('Библиотека',{exact:true}).selectOption(lid);
   await dlg.getByRole('button',{name:'Annex 65',exact:true}).waitFor();
   await dlg.locator('.library-items input[type="checkbox"]').first().check();
   await dlg.getByRole('button',{name:'Добавить выбранные · 1',exact:true}).click();
   await dlg.getByLabel('Режим Annex 65',{exact:true}).selectOption('passthrough');
   await dlg.getByLabel('Номер Annex 65',{exact:true}).fill('12');
   await dlg.getByLabel('Папка Annex 65',{exact:true}).fill('Evidence');
   await dlg.getByLabel('Имя PDF Annex 65',{exact:true}).fill('Annex 12.pdf');
   await dlg.getByRole('button',{name:'Проверить назначения',exact:true}).click();
   await dlg.getByRole('button',{name:'Добавить в подачу',exact:true}).click();
   await dlg.getByText('Материалы добавлены. Вернитесь в подачу, просмотрите результат и подтвердите подготовку.',{exact:true}).waitFor();
   await dlg.getByRole('button',{name:'Вернуться к подаче',exact:true}).click();
   await page.getByRole('button',{name:/Проверка и экспорт/}).click();
   const download=page.waitForEvent('download');await page.getByRole('button',{name:'Собрать подачу · ZIP',exact:true}).click();
   await(await download).saveAs(path.join(dir,'ui-library-ready.zip'));
   assert.deepEqual(await request('/projects/'+sourcePid),sourceBefore);libraryUi=true;
  }
  const report={result:'passed',browser:'Chromium',browser_plugin:'not available',page_identity:true,
   blank_page:false,framework_overlay:false,option_count:107,numeric_order:true,stable_targets_reload_scan:true,
   service_options:true,all_same:true,manual_add:true,stored_order_unchanged:true,
   ready_upload_preview_export:true,library_ready_ui:libraryUi,viewports:[1440,760,390],errors};
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(dir,'ready-ui-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
