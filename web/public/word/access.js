// A separate WebView grant, issued only after the owner confirms its visible code.
export class WordAccess {
  constructor({fetch = globalThis.fetch, storage, onPairing = () => {}, wait = ms => new Promise(resolve=>setTimeout(resolve,ms))} = {}) {
    this.rawFetch=fetch.bind(globalThis);this.onPairing=onPairing;this.wait=wait;
    this.token=null;this.inflight=null;this.grant=null;
    try {this.storage=storage===undefined?globalThis.localStorage:storage;this.grant=this.storage?.getItem('bl-exhibit-word-grant-v1');} catch { /* Pairing still works without storage. */ }
  }
  async control(path, body) {
    const response=await this.rawFetch('/api/access/word/'+path,{method:'POST',
      headers:{'X-Exhibit-Local':'1','Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(4000)});
    if(!response.ok){const error=new Error('Разрешение панели не подтверждено или код истёк. Повторите подключение панели.');error.status=response.status;throw error;}
    return response.json();
  }
  save(grant) {
    this.grant=grant;
    try {if(grant)this.storage?.setItem('bl-exhibit-word-grant-v1',grant);else this.storage?.removeItem('bl-exhibit-word-grant-v1');} catch { /* In-memory grant remains usable. */ }
  }
  async authorize() {
    if(this.token)return;
    if(!this.inflight)this.inflight=this.establish().finally(()=>{this.inflight=null;});
    return this.inflight;
  }
  async establish() {
    if(this.grant) {
      try {this.token=(await this.control('session',{grant:this.grant})).token;return;}
      catch(error) {
        // Network failures keep the grant for the next reconnect; only a server refusal revokes it.
        if(error.status!==401&&error.status!==400)throw error;
        this.save(null);
      }
    }
    const request=await this.control('request',{});
    this.onPairing(request.code);
    for(let n=0;n<120;n++) {
      await this.wait(1500);
      const result=await this.control('poll',{id:request.id,secret:request.secret});
      if(result.pending)continue;
      this.save(result.grant);this.token=result.token;this.onPairing(null);return;
    }
    throw new Error('Код панели истёк. Нажмите «Повторить подключение», чтобы получить новый код.');
  }
  async fetch(path, options={}) {
    await this.authorize();
    const send=()=>this.rawFetch(path,{...options,headers:{...options.headers,Authorization:'Bearer '+this.token}});
    let response=await send();
    if(response.status===401&&!options.keepalive) {this.token=null;await this.authorize();response=await send();}
    return response;
  }
}
