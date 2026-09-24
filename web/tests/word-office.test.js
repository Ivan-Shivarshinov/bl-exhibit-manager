import {test} from 'node:test';
import assert from 'node:assert/strict';
import {insertCitation, inspectUpdates, applyUpdates} from '../public/word/office.js';
import {citationUrl} from '../public/word/core.js';

const catalog={id:'a'.repeat(32),documents:[{id:'b'.repeat(32),identifier:'RLA-1',full:'RLA-1, Code'}],style:{separator:'; ',locator_separator:', '}};

function host(relations, bodyType='NoteItem', tracking='Off') {
  const writes=[];
  const notes=relations.map((relation,index)=>({body:{
    text:'Existing citation', load(){}, getRange(where){
      return {index,where,insertOoxml(xml,location){writes.push({index,where,xml,location});}};
    },
  }}));
  const selection={parentBody:{type:bodyType,load(){}},compareLocationWith(range){return {value:relations[range.index]};}};
  const context={sync:async()=>{},document:{load(){},changeTrackingMode:tracking,getSelection:()=>selection,body:{footnotes:{items:notes,load(){}}}}};
  globalThis.Word={run:callback=>callback(context)};
  globalThis.location={origin:'https://localhost:8769'};
  return writes;
}

test('append uses membership in the selected footnote even for NoteItem bodies',async()=>{
  const writes=host(['Unrelated','Inside']);
  await insertCitation(catalog,catalog.documents[0].id,'full','paras. 5–6',true);
  assert.equal(writes.length,1);
  assert.equal(writes[0].index,1);
  assert.equal(writes[0].where,'End');
  assert.match(writes[0].xml,/<w:hyperlink /);
  assert.match(writes[0].xml,/paras. 5–6/);
});

test('main document and endnote selections cannot append to a footnote',async()=>{
  for(const type of ['MainDoc','Endnote','NoteItem']){
    const writes=host(['Unrelated','Unrelated'],type);
    await assert.rejects(insertCitation(catalog,catalog.documents[0].id,'full','',true),/курсор/);
    assert.equal(writes.length,0);
  }
});

test('tracked changes block mutation before creating citation XML',async()=>{
  const writes=host(['Inside'],'NoteItem','TrackAll');
  await assert.rejects(insertCitation(catalog,catalog.documents[0].id,'full','',true),/исправлений/);
  assert.equal(writes.length,0);
});

function updateHost() {
  const writes=[], state={xml:'original',tracking:'Off'};
  const ranges=[0,1].map(i=>({text:'RLA-1, Old',hyperlink:citationUrl('https://localhost:8769',catalog.id,catalog.documents[0].id,'RLA-1, Old','full',String(i).repeat(32)),
    getOoxml:()=>({value:state.xml}),insertOoxml:(xml)=>writes.push({i,xml})}));
  globalThis.Word={run:fn=>fn({sync:async()=>{},document:{load(){},get changeTrackingMode(){return state.tracking;},
    body:{footnotes:{load(){},items:[{body:{getRange:()=>({getHyperlinkRanges:()=>({load(){},items:ranges})})}}]}}}})};
  globalThis.location={origin:'https://localhost:8769'};
  return {writes,state,ranges};
}

test('prepare all updates before writes; refusal of one leaves every citation untouched',async()=>{
  const {writes}=updateHost(); const plan=await inspectUpdates(catalog);
  let count=0;
  globalThis.fetch=async()=>({ok:++count===1,json:async()=>({ooxml:'prepared',detail:'Mixed formatting'})});
  await assert.rejects(applyUpdates(catalog,plan,[0,1]),/Mixed formatting/);
  assert.deepEqual(writes,[]);
});

test('formatting or tracking change during preparation prevents all writes',async()=>{
  for(const mutation of ['format','tracking']) {
    const {writes,state}=updateHost(); const plan=await inspectUpdates(catalog);
    globalThis.fetch=async()=>{
      if(mutation==='format') state.xml='edited'; else state.tracking='TrackAll';
      return {ok:true,json:async()=>({ooxml:'prepared'})};
    };
    await assert.rejects(applyUpdates(catalog,plan,[0]),/изменилось|исправлений/);
    assert.deepEqual(writes,[]);
  }
});

test('successful updates apply prepared source formatting in reverse document order',async()=>{
  const {writes}=updateHost(); const plan=await inspectUpdates(catalog);
  globalThis.fetch=async(_,request)=>{
    assert.equal(JSON.parse(request.body).ooxml,'original');
    return {ok:true,json:async()=>({ooxml:'preserved typography'})};
  };
  assert.equal(await applyUpdates(catalog,plan,[0,1]),2);
  assert.deepEqual(writes,[{i:1,xml:'preserved typography'},{i:0,xml:'preserved typography'}]);
});
