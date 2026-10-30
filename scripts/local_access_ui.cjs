// Real Chromium and real HTTP/TLS. Office globals below are an explicit simulation.
const {chromium}=require('../web/node_modules/playwright');
const http=require('node:http');
const enterApp=require('./local_browser_access.cjs');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const base=process.env.ACCESS_QA_URL,wordBase=process.env.ACCESS_QA_WORD,out=process.env.ACCESS_QA_OUTPUT,entry=process.env.ACCESS_QA_ENTRY;
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function login(page) {
 const ready=page.waitForResponse(r=>r.url()===base+'/api/access/status'&&r.status()===200);
 await page.goto(JSON.parse(fs.readFileSync(entry,'utf8')).first);await ready;
 await page.getByRole('button',{name:'Помощь и настройка',exact:true}).click();
}
(async()=>{
 // Full Chromium's new headless mode includes the PDF viewer; headless_shell downloads PDFs.
 const browser=await chromium.launch({headless:true,channel:'chromium'});
 const pages=[];
 try {
  const owner=await browser.newContext({ignoreHTTPSErrors:true}),pane=await browser.newContext({ignoreHTTPSErrors:true});
  const page=await owner.newPage(),word=await pane.newPage();
  pages.push(page,word);
  await login(page);
  await pane.route('https://appsforoffice.microsoft.com/**',r=>r.fulfill({contentType:'text/javascript',body:'/* Explicit simulated Office host for access-control acceptance. */'}));
  await pane.addInitScript(()=>{
   window.Office={HostType:{Word:'Word'},onReady:callback=>callback({host:'Word'}),context:{requirements:{isSetSupported:()=>true}}};
   window.Word={run:async callback=>callback({document:{load(){}},sync:async()=>{}})};
  });
  await word.goto(wordBase+'/word/index.html');
  await word.locator('#access-code').filter({hasText:/[0-9A-F]{4}-[0-9A-F]{4}/}).waitFor();
  let code=await word.locator('#access-code').textContent();
  assert.equal(await word.locator('#workspace').isVisible(),false);
  assert.equal((await pane.request.get(wordBase+'/api/projects')).status(),401);
  await page.getByRole('button',{name:'Разрешить код '+code,exact:true}).click();
  await word.locator('#workspace').waitFor();
  await word.locator('#project option').filter({hasText:'Synthetic access control'}).waitFor({state:'attached'});
  await word.screenshot({path:path.join(out,'paired-word-simulation.png')});
  await page.getByRole('button',{name:'Вернуться к работе',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.paper img')?.naturalWidth>0);
  const downloading=page.waitForEvent('download');await page.getByRole('link',{name:'Скачать PDF',exact:true}).click();
  const download=await downloading;await download.saveAs(path.join(out,'owner-preview.pdf'));
  assert(fs.readFileSync(path.join(out,'owner-preview.pdf')).subarray(0,5).equals(Buffer.from('%PDF-')));
  await page.getByRole('button',{name:'Помощь и настройка',exact:true}).click();
  const doc='/api/word/open/'+[process.env.ACCESS_QA_PID,process.env.ACCESS_QA_DID,'f'.repeat(32)].join('/');
  const external=await owner.newPage();pages.push(external);
  let recordSignature;const signature=new Promise(resolve=>{recordSignature=resolve;});
  await external.exposeFunction('__accessQaSignature',recordSignature);
  await external.addInitScript(target=>{
   const raw=Response.prototype.blob;
   Response.prototype.blob=async function(){const blob=await raw.call(this);if(this.url===target)await window.__accessQaSignature(Array.from(new Uint8Array(await blob.slice(0,5).arrayBuffer())));return blob;};
  },base+doc);
  // A real new browser tab follows TLS -> HTTP UI -> authenticated PDF -> Blob URL.
  const externalPdf=external.waitForResponse(r=>r.url()===base+doc&&r.status()===200);
  await external.goto(wordBase+doc);
  const pdf=await externalPdf;assert.match(pdf.headers()['content-type'],/application\/pdf/);
  // Chromium can expose the PDF viewer's HTML instead of bytes through CDP Response.body.
  // Measure the actual fetched Blob before navigation; no request or credential is replaced.
  assert.deepEqual(await signature,Array.from(Buffer.from('%PDF-')));
  await external.waitForURL(url=>url.protocol==='blob:');await external.screenshot({path:path.join(out,'protected-pdf.png')});await external.close();
  assert.equal((await pane.request.get(wordBase+'/api/projects')).status(),401);
  assert.deepEqual(await owner.cookies(),[]);
  // A hostile service on another local port receives no credential and cannot read the owner's origin storage.
  const seen=[],neighbor=http.createServer((request,response)=>{seen.push(request.headers);response.end('<!doctype html><title>Untrusted local service</title>');});
  await new Promise(resolve=>neighbor.listen(0,'127.0.0.1',resolve));
  try{
   const hostile=await owner.newPage();await hostile.goto('http://127.0.0.1:'+neighbor.address().port);
   assert.equal(await hostile.evaluate(()=>localStorage.getItem('bl-exhibit-owner-v1')),null);
   assert(seen.every(headers=>!headers.authorization&&!headers.cookie));await hostile.close();
  }finally{await new Promise(resolve=>neighbor.close(resolve));}
  console.log('RESTART_SERVER');
  for(let n=0;n<200;n++){if(JSON.parse(fs.readFileSync(entry,'utf8')).restarted)break;await wait(100);if(n===199)throw Error('Restart timeout');}
  await login(page);
  await word.getByRole('button',{name:'Обновить список',exact:true}).click();
  await word.locator('#project option').filter({hasText:'Synthetic access control'}).waitFor({state:'attached'});
  assert.deepEqual(await(await enterApp.request(page,base).get(base+'/api/access/word/requests')).json(),[]);
  await page.getByRole('button',{name:'Отозвать разрешения панелей',exact:true}).click();
  await page.getByText(/Разрешения панелей отозваны/).waitFor();
  // Presence or an in-flight refresh detects revocation; the workspace is hidden while pairing.
  await word.locator('#access-code').filter({hasText:/[0-9A-F]{4}-[0-9A-F]{4}/}).waitFor();
  const next=await word.locator('#access-code').textContent();assert.notEqual(next,code);
  assert.equal(await word.locator('#workspace').isVisible(),false);
  await page.getByRole('button',{name:'Разрешить код '+next,exact:true}).click();
  await word.locator('#workspace').waitFor();
  await page.screenshot({path:path.join(out,'owner-permissions.png')});
  const report={result:'passed',browser:'Chromium headless',word_host:'explicit simulation, not native Word',checks:['anonymous HTTP/TLS denied','private-file owner bootstrap','matching-code UI approval','scoped Word access','protected external PDF handoff','origin-bound storage; no credential at adjacent port','restart without repeated approval','revocation and explicit reapproval'],system_trust_changed:false};
  fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }catch(error){for(let i=0;i<pages.length;i++){await pages[i].screenshot({path:path.join(out,'failure-'+i+'.png')}).catch(()=>{});console.error(await pages[i].locator('body').innerText().catch(()=>''));}throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
