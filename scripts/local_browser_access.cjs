// Exercise the production private-file handoff, without bypassing API authorization.
const entered=new WeakSet();
async function enterApp(page,base) {
 if(!entered.has(page.context())) {
  const entry=process.env.EXHIBIT_BROWSER_ENTRY;
  if(!entry||new URL(entry).protocol!=='file:')throw new Error('Prepare a private browser_entry for this isolated test');
  const ready=page.waitForResponse(r=>r.url()===base+'/api/access/status'&&r.status()===200);
  await page.goto(entry);await ready;entered.add(page.context());
 }
 await page.goto(base);
}
enterApp.request=(page,base)=>Object.fromEntries(['get','post','patch','delete','put','fetch'].map(method=>[method,async(url,options={})=>{
 if(new URL(url).origin!==new URL(base).origin||!new URL(url).pathname.startsWith('/api/'))throw Error('Owner credential cannot leave the API origin');
 const token=await page.evaluate(()=>localStorage.getItem('bl-exhibit-owner-v1'));
 if(!token)throw Error('Browser did not establish its owner session');
 return page.request[method](url,{...options,maxRedirects:0,headers:{...options.headers,'X-Exhibit-Local':'1',Authorization:'Bearer '+token}});
}]));
module.exports=enterApp;
