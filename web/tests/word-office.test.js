import {test} from 'node:test';
import assert from 'node:assert/strict';
import {insertCitation} from '../public/word/office.js';

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
