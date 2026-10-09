import {test} from 'node:test';
import assert from 'node:assert/strict';
import {startWordConnection} from '../public/word/connection.js';

function fixture(supported=true) {
  const calls=[], errors=[], loads=[], timers=new Map();let counter=0, failure=null;
  const deps={
    office:{HostType:{Word:'Word'},context:{requirements:{isSetSupported:()=>supported}}},
    word:{run:async fn=>fn({document:{load:name=>loads.push(name)},sync:async()=>{}})},
    location:{protocol:'https:'},
    fetch:async (url,options)=>{
      const body=JSON.parse(options.body);calls.push(body);
      if(failure){const value=failure;failure=null;if(value instanceof Error)throw value;return {ok:false,status:value};}
      return {ok:true,json:async()=>body.action==='open'?{token:'test-session'}:{active:true}};
    },
    schedule:fn=>{timers.set(++counter,fn);return counter;},cancel:id=>timers.delete(id),
    onError:message=>errors.push(message),
  };
  async function tick() {const [id,fn]=timers.entries().next().value;timers.delete(id);await fn();}
  return {deps,calls,errors,loads,timers,tick,fail:(value=400)=>{failure=value;}};
}

test('browser, other Office hosts and HTTP never announce a Word connection',async()=>{
  for(const kind of ['browser','Excel','http']) {
    const f=fixture();if(kind==='http')f.deps.location.protocol='http:';
    const stop=await startWordConnection({host:kind==='http'?'Word':kind},f.deps);stop();
    assert.deepEqual(f.calls,[]);assert.deepEqual(f.loads,[]);assert.equal(f.timers.size,0);
  }
});

test('ready Word is probed without text, stays active, and closes its own session',async()=>{
  const f=fixture();const stop=await startWordConnection({host:'Word'},f.deps);
  assert.deepEqual(f.loads,['saved']);
  assert.deepEqual(f.calls,[{action:'open',host:'Word',supported:true}]);
  await f.tick();assert.deepEqual(f.calls[1],{action:'ping',token:'test-session'});
  stop();assert.deepEqual(f.calls[2],{action:'close',token:'test-session'});
  assert.equal(f.timers.size,0);
});

test('runtime failure does not claim presence',async()=>{
  const f=fixture();f.deps.word.run=async()=>{throw new Error('Word unavailable');};
  await assert.rejects(startWordConnection({host:'Word'},f.deps),/Word unavailable/);
  assert.deepEqual(f.calls,[]);assert.equal(f.timers.size,0);
});

test('unsupported Word reports capability failure without document access',async()=>{
  const f=fixture(false);const stop=await startWordConnection({host:'Word'},f.deps);
  assert.deepEqual(f.loads,[]);
  assert.deepEqual(f.calls[0],{action:'open',host:'Word',supported:false});stop();
});

test('expired lease after suspension or app restart opens a fresh session',async()=>{
  const f=fixture();const stop=await startWordConnection({host:'Word'},f.deps);
  f.fail();await f.tick();assert.equal(f.errors.length,1);
  await f.tick();assert.deepEqual(f.calls[2],{action:'open',host:'Word',supported:true});stop();
});

test('network failure retries without overlapping requests or discarding the lease',async()=>{
  const f=fixture();const stop=await startWordConnection({host:'Word'},f.deps);
  f.fail(new Error('offline'));await f.tick();await f.tick();
  assert.deepEqual(f.calls[2],{action:'ping',token:'test-session'});
  assert.equal(f.timers.size,1);assert.equal(f.errors.length,1);stop();
});
