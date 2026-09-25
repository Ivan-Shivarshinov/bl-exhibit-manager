import {test} from 'node:test';
import assert from 'node:assert/strict';
import {insertCitation, inspectUpdates, applyUpdates} from '../public/word/office.js';
import {citationUrl} from '../public/word/core.js';
import {citationOoxml} from '../public/word/citation-ooxml.js';
import {DOMParser} from '@xmldom/xmldom';

globalThis.DOMParser = DOMParser;
const fragment = citationOoxml('RLA-1, Old', 'RLA-1', 'https://localhost:8769/old');

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
  const writes=[], state={xml:fragment,tracking:'Off'};
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
      if(mutation==='format') state.xml=fragment.replace('000000','FF0000'); else state.tracking='TrackAll';
      return {ok:true,json:async()=>({ooxml:'prepared'})};
    };
    await assert.rejects(applyUpdates(catalog,plan,[0]),/изменилось|исправлений/);
    assert.deepEqual(writes,[]);
  }
});

test('successful updates apply prepared source formatting in reverse document order',async()=>{
  const {writes}=updateHost(); const plan=await inspectUpdates(catalog);
  globalThis.fetch=async(_,request)=>{
    assert.equal(JSON.parse(request.body).ooxml,fragment);
    return {ok:true,json:async()=>({ooxml:'preserved typography'})};
  };
  assert.equal(await applyUpdates(catalog,plan,[0,1]),2);
  assert.deepEqual(writes,[{i:1,xml:'preserved typography'},{i:0,xml:'preserved typography'}]);
});

test('restored project updates tolerate regenerated Word IDs without changing order or prepared typography',async()=>{
  const {writes,state,ranges}=updateHost();
  const copy={...catalog,id:'c'.repeat(32),restored_bindings:ranges.map(range=>({
    project:catalog.id,document:catalog.documents[0].id,citation:new URL(range.hyperlink).pathname.split('/').at(-1),form:'full',expected:range.text,
  }))};
  const plan=await inspectUpdates(copy);
  assert.ok(plan.rows.every(row=>row.status==='update'));
  globalThis.fetch=async(_,request)=>{
    const body=JSON.parse(request.body);
    assert.match(body.address,new RegExp('/'+copy.id+'/'));
    state.xml=fragment.replace('<w:p>', '<w:p w:rsidR="F0F0F0F0">').replaceAll('rCitation','rId18');
    return {ok:true,json:async()=>({ooxml:'preserved copy typography'})};
  };
  assert.equal(await applyUpdates(copy,plan,[0,1]),2);
  assert.deepEqual(writes,[{i:1,xml:'preserved copy typography'},{i:0,xml:'preserved copy typography'}]);
});

test('a text or destination change during preparation prevents every write',async()=>{
  for(const property of ['text','hyperlink']) {
    const {writes,ranges}=updateHost(); const plan=await inspectUpdates(catalog);
    globalThis.fetch=async()=>{
      ranges[1][property]='user edit';
      return {ok:true,json:async()=>({ooxml:'prepared'})};
    };
    await assert.rejects(applyUpdates(catalog,plan,[0,1]),/Текст Word изменился/);
    assert.deepEqual(writes,[]);
  }
});
