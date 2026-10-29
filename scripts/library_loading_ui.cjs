// Exercise the real React dialog with controlled network ordering, without user data.
const {before,after,test}=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const path=require('node:path');
const {build}=require('../web/node_modules/esbuild');
const {chromium}=require('../web/node_modules/playwright');

let server,browser,base;
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
before(async()=>{
 const bundle=await build({stdin:{contents:`
  import React from 'react';
  import {createRoot} from 'react-dom/client';
  import MaterialLibrary from './src/MaterialLibrary.jsx';
  async function api(path,body,options={}) {
   const response=await fetch('/api'+path,{method:body===undefined?'GET':'POST',body:body===undefined?undefined:JSON.stringify(body),...options});
   if(!response.ok)throw new Error('Catalog request failed');
   return response.json();
  }
  createRoot(document.getElementById('root')).render(React.createElement(MaterialLibrary,{api,onClose:()=>{}}));
 `,resolveDir:path.resolve(__dirname,'../web'),loader:'jsx'},bundle:true,write:false,loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'}});
 server=http.createServer((req,res)=>{
  res.setHeader('Content-Type',req.url==='/app.js'?'text/javascript':'text/html');
  res.end(req.url==='/app.js'?bundle.outputFiles[0].contents:'<div id="root"></div><script type="module" src="/app.js"></script>');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 base=`http://127.0.0.1:${server.address().port}`;
 browser=await chromium.launch(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{});
});
after(async()=>{if(browser)await browser.close();if(server)await new Promise(resolve=>server.close(resolve));});

async function fixture(t,{empty=false,catalogError=false,createError=false}={}) {
 const page=await browser.newPage();t.after(()=>page.close());
 const listGate=deferred(),catalogGate=deferred(),catalogStarted=deferred(),postGate=deferred(),postStarted=deferred();
 const libraries=empty?[]:[{id:'old',name:'Existing library'}],posts=[],errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const request=route.request(),url=new URL(request.url());
  let body,status=200;
  if(url.pathname==='/api/libraries'&&request.method()==='GET'){
   await listGate.promise;body=libraries;
  }else if(url.pathname==='/api/libraries'&&request.method()==='POST'){
   const submitted=JSON.parse(request.postData());posts.push(submitted);postStarted.resolve();await postGate.promise;
   if(createError){status=500;body={};}else{body={id:'new',name:submitted.name};libraries.push(body);}
  }else if(url.pathname.endsWith('/materials')){
   const id=url.pathname.split('/')[3];
   if(id==='old'){catalogStarted.resolve();await catalogGate.promise;if(catalogError)status=500;}
   body={library:{id,revision:1},items:[],total:0};
  }else{throw new Error('Unexpected API request: '+url.pathname);}
  // An obsolete catalog request may have been aborted after creating a library.
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)}).catch(e=>{
   if(!/closed|disposed|cancel|abort/i.test(e.message))throw e;
  });
 });
 t.after(()=>{listGate.resolve();catalogGate.resolve();postGate.resolve();});
 await page.goto(base);
 const input=page.getByLabel('Название новой библиотеки'),button=page.getByRole('button',{name:'Создать библиотеку',exact:true});
 await input.waitFor();assert(await input.isDisabled(),'Initial library discovery must finish first');
 listGate.resolve();
 if(!empty)await catalogStarted.promise;
 await page.waitForFunction(()=>document.querySelector('.library-dialog select option[value="old"]')||document.querySelector('.library-dialog input:not(:disabled)'));
 return {page,input,button,catalogGate,postGate,postStarted,posts,errors};
}

test('library name stays editable while the selected catalog loads; creation does not wait for it',async t=>{
 const f=await fixture(t);
 assert.equal(await f.input.isEnabled(),true,'Catalog loading must not disable the independent create form');
 await f.input.fill('New independent library');
 assert.equal(await f.input.inputValue(),'New independent library');
 await f.button.click();await f.postStarted.promise;
 assert(await f.input.isDisabled());assert(await f.button.isDisabled());
 assert.deepEqual(f.posts,[{name:'New independent library'}]);
 f.postGate.resolve();
 await f.page.waitForFunction(()=>document.querySelector('select[aria-label="Библиотека"]').value==='new');
 f.catalogGate.resolve();
 await f.page.getByText('Библиотека пока пуста. Сохраните материал из подачи.',{exact:true}).waitFor();
 assert.equal(await f.input.inputValue(),'');assert.deepEqual(f.errors,[]);
});

test('catalog completion preserves typed text and creation sends it once',async t=>{
 const f=await fixture(t);
 assert(await f.input.isEnabled(),'Catalog loading must not disable name entry');
 await f.input.fill('A name typed before catalog completion');
 f.catalogGate.resolve();
 await f.page.getByText('Библиотека пока пуста. Сохраните материал из подачи.',{exact:true}).waitFor();
 assert.equal(await f.input.inputValue(),'A name typed before catalog completion');
 await f.button.click();await f.postStarted.promise;f.postGate.resolve();
 await f.page.waitForFunction(()=>document.querySelector('select[aria-label="Библиотека"]').value==='new');
 assert.equal(f.posts.length,1);assert.deepEqual(f.errors,[]);
});

test('catalog failure and failed creation preserve the name for a later attempt',async t=>{
 const f=await fixture(t,{catalogError:true,createError:true});
 assert(await f.input.isEnabled(),'An unrelated catalog must not block creation');
 await f.input.fill('Keep this name');f.catalogGate.resolve();
 await f.page.getByRole('alert').waitFor();
 assert.equal(await f.input.inputValue(),'Keep this name');
 await f.button.click();await f.postStarted.promise;f.postGate.resolve();
 await f.page.waitForFunction(()=>!document.querySelector('input[aria-label="Название новой библиотеки"]').disabled);
 assert.equal(await f.input.inputValue(),'Keep this name');assert.equal(f.posts.length,1);assert.deepEqual(f.errors,[]);
});

test('the first library can be created from an empty catalog',async t=>{
 const f=await fixture(t,{empty:true});
 await f.input.fill('First library');await f.button.click();await f.postStarted.promise;f.postGate.resolve();
 await f.page.waitForFunction(()=>document.querySelector('select[aria-label="Библиотека"]').value==='new');
 assert.deepEqual(f.posts,[{name:'First library'}]);assert.deepEqual(f.errors,[]);
});
