// Browser credentials are origin-bound. Cookies would disclose them to another loopback port.
export const OWNER_STORAGE='bl-exhibit-owner-v1';
let memoryToken=null,memoryOrigin=null,memoryFallback=false;
function storage(options) {try{return options.storage===undefined?globalThis.localStorage:options.storage;}catch{return null;}}
export function ownerToken(options={}) {
  const origin=(options.location||globalThis.location)?.origin;
  if(memoryFallback&&memoryOrigin===origin)return memoryToken;
  try {const token=storage(options)?.getItem(OWNER_STORAGE);if(token)return token;}catch {memoryFallback=true;}
  return memoryFallback&&memoryOrigin===origin?memoryToken:null;
}
function remember(token,options) {
  memoryToken=token;memoryOrigin=(options.location||globalThis.location)?.origin;
  try {const target=storage(options);if(!target)throw Error('Storage unavailable');target.setItem(OWNER_STORAGE,token);memoryFallback=false;}
  catch {memoryFallback=true;}
}
export async function ownerFetch(path,options={},environment={}) {
  const location=environment.location||globalThis.location;
  const url=new URL(path,location.origin);
  if(url.origin!==location.origin||!url.pathname.startsWith('/api/'))throw new Error('Недопустимый адрес локального запроса.');
  const token=ownerToken(environment);
  if(!token)throw new Error('Сеанс недоступен. Запустите приложение обычным способом ещё раз.');
  return (environment.fetch||globalThis.fetch)(url.pathname+url.search,{...options,credentials:'omit',redirect:'error',
    headers:{...options.headers,'X-Exhibit-Local':'1',Authorization:'Bearer '+token}});
}
// The fragment is one-use and short-lived; the private owner key never enters the browser.
export async function establishBrowserSession(options={}) {
  const fetch=options.fetch||globalThis.fetch,location=options.location||globalThis.location,history=options.history||globalThis.history;
  const fragment=new URLSearchParams(location.hash.slice(1)),bootstrap=fragment.get('access');
  let bootstrapFailed=false;
  if(bootstrap) {
    fragment.delete('access');
    history.replaceState(null,'',location.pathname+location.search+(fragment.size?'#'+fragment:''));
    const response=await fetch('/api/access/session',{method:'POST',credentials:'omit',redirect:'error',
      headers:{'Content-Type':'application/json','X-Exhibit-Local':'1'},body:JSON.stringify({bootstrap})});
    bootstrapFailed=!response.ok;
    if(response.ok){const data=await response.json();if(typeof data.token!=='string')throw new Error('Некорректный ответ запуска.');remember(data.token,options);}
  }
  let response;
  try{response=await ownerFetch('/api/access/status',{},options);}catch{}
  if(!response?.ok)throw new Error(bootstrapFailed?'Ссылка запуска истекла или уже использована. Запустите приложение обычным способом ещё раз.':'Запустите Start.cmd или BLExhibitManager.exe на Windows, BLExhibitManager.app на Mac. Приложение само откроет доступ в браузере. Затем можно обновить эту страницу.');
}
export async function openWordResource() {
  const target=new URLSearchParams(globalThis.location.search).get('open');
  if(!target)return false;
  if(!new RegExp('^/api/word/open/[a-f0-9]{32}/[a-f0-9]{32}/[A-Za-z0-9_-]{1,128}$').test(target))throw new Error('Некорректная ссылка Word.');
  const response=await ownerFetch(target);
  if(!response.ok){let data;try{data=await response.json();}catch{}throw new Error(data?.detail||'Не удалось открыть PDF по ссылке Word.');}
  const blob=await response.blob();globalThis.location.replace(URL.createObjectURL(blob));return true;
}
export async function downloadResource(path,name) {
  const response=await ownerFetch(path);
  if(!response.ok){let data;try{data=await response.json();}catch{}throw new Error(data?.detail||'Файл недоступен.');}
  const disposition=response.headers.get('Content-Disposition')||'';
  let filename=name;
  if(!filename){const encoded=/filename\*=UTF-8''([^;]+)/i.exec(disposition),plain=/filename="([^"]+)"/i.exec(disposition);try{filename=encoded?decodeURIComponent(encoded[1]):plain?.[1];}catch{filename=plain?.[1];}}
  const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download=filename||'download';
  link.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
}
