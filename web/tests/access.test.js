import test from 'node:test';
import assert from 'node:assert/strict';
import {establishBrowserSession,ownerFetch} from '../src/access.js';
import {WordAccess} from '../public/word/access.js';

const response=(body,status=200)=>({ok:status===200,status,json:async()=>body});
const storage=value=>({getItem:()=>value,setItem:(key,next)=>{value=next;},removeItem:()=>{value=null;}});

test('browser consumes a one-use fragment before any request and checks the session',async()=>{
 const calls=[],history={replaceState:(_state,_title,path)=>calls.push(['clear',path])};
 const saved=storage(null);
 await establishBrowserSession({location:{origin:'http://127.0.0.1:8765',hash:'#access=short-lived&tab=docs',pathname:'/',search:''},history,storage:saved,
  fetch:async(path,options)=>{calls.push([path,options]);return response({token:'owner-session'});}});
 assert.deepEqual(calls[0],['clear','/#tab=docs']);
 assert.equal(calls[1][0],'/api/access/session');assert.deepEqual(JSON.parse(calls[1][1].body),{bootstrap:'short-lived'});
 assert.equal(calls[2][0],'/api/access/status');
 assert.equal(calls[2][1].headers.Authorization,'Bearer owner-session');assert.equal(calls[2][1].credentials,'omit');
 assert.equal(saved.getItem(),'owner-session');
});

test('opening a bare root never creates a session or authorizes an anonymous visitor',async()=>{
 const calls=[];
 await assert.rejects(establishBrowserSession({location:{origin:'http://127.0.0.1:8765',hash:'',pathname:'/',search:''},history:{},storage:storage(null),
  fetch:async path=>{calls.push(path);return response({},401);}}),/Start.cmd/);
 assert.deepEqual(calls,[]);
});

test('owner credential never follows another origin, non-API resource, redirect or cookie',async()=>{
 const calls=[],environment={location:{origin:'http://127.0.0.1:8765'},storage:storage('owner-only'),fetch:async(url,options)=>{calls.push(options);return response({});}};
 await assert.rejects(ownerFetch('http://127.0.0.1:8766/api/projects',{},environment));
 await assert.rejects(ownerFetch('/static/image.png',{},environment));
 assert.equal(calls.length,0);
 await ownerFetch('/api/projects',{credentials:'include',redirect:'follow'},environment);
 assert.equal(calls[0].credentials,'omit');assert.equal(calls[0].redirect,'error');
});

test('Word has no data access until the owner approves and concurrent callers share pairing',async()=>{
 const calls=[],codes=[],saved=storage(null);let polls=0;
 const access=new WordAccess({storage:saved,onPairing:code=>codes.push(code),wait:async()=>{},fetch:async(path,options)=>{
  calls.push(path);
  if(path.endsWith('/request'))return response({id:'request',secret:'secret',code:'ABCD-1234'});
  if(path.endsWith('/poll'))return response(++polls===1?{pending:true}:{grant:'word-only',token:'session'});
  assert.equal(options.headers.Authorization,'Bearer session');return response([]);
 }});
 await Promise.all([access.fetch('/api/projects'),access.fetch('/api/projects')]);
 assert.deepEqual(codes,['ABCD-1234',null]);assert.equal(calls.filter(p=>p.endsWith('/request')).length,1);
 assert.deepEqual(calls.slice(0,3),['/api/access/word/request','/api/access/word/poll','/api/access/word/poll']);
 assert.equal(saved.getItem(),'word-only');
});

test('approved Word recovers a per-process session after restart without new approval',async()=>{
 let sessions=0,requests=0;
 const access=new WordAccess({storage:storage('approved'),onPairing:()=>assert.fail('Unnecessary pairing'),fetch:async(path,options)=>{
  if(path.endsWith('/session')){sessions++;assert.equal(JSON.parse(options.body).grant,'approved');return response({token:'session-'+sessions});}
  assert.equal(options.headers.Authorization,'Bearer session-'+sessions);return response([],++requests===1?401:200);
 }});
 assert.equal((await access.fetch('/api/projects')).status,200);assert.equal(sessions,2);
});

test('revoked grants require a new matching-code approval',async()=>{
 let code;
 const access=new WordAccess({storage:storage('revoked'),onPairing:value=>{if(value)code=value;},wait:async()=>{},fetch:async path=>{
  if(path.endsWith('/session'))return response({},401);
  if(path.endsWith('/request'))return response({id:'new',secret:'new-secret',code:'NEW1-NEW2'});
  if(path.endsWith('/poll'))return response({grant:'new-grant',token:'new-session'});
  return response([]);
 }});
 await access.fetch('/api/projects');assert.equal(code,'NEW1-NEW2');assert.equal(access.grant,'new-grant');
});

test('temporary server failure preserves the approved grant; blocked storage still permits pairing',async()=>{
 const saved=storage('approved');
 const failed=new WordAccess({storage:saved,fetch:async()=>response({},503)});
 await assert.rejects(failed.authorize());assert.equal(saved.getItem(),'approved');
 const access=new WordAccess({storage:{getItem:()=>{throw Error('blocked');}},wait:async()=>{},fetch:async path=>
  response(path.endsWith('/request')?{id:'request',secret:'secret',code:'1234-ABCD'}:{grant:'temporary',token:'session'})});
 await access.authorize();assert.equal(access.token,'session');
});
