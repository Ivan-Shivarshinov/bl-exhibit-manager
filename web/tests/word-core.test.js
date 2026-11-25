import {test} from 'node:test';
import assert from 'node:assert/strict';
import {citationUrl,parseCitation,updatePlan,verifyPlan} from '../public/word/core.js';
const pid='a'.repeat(32),did='b'.repeat(32),cid='c'.repeat(32),origin='https://localhost:8769';
const address=citationUrl(origin,pid,did,'Old title','full',cid);
const catalog={id:pid,documents:[{id:did,full:'New title',short:'New'}]};
test('stable IDs and Unicode survive address encoding',()=>{
 const url=citationUrl(origin,pid,did,'Кодекс & Code','short',cid);
 assert.equal(parseCitation(url).expected,'Кодекс & Code');assert.equal(parseCitation(url).citation,cid);
 assert.equal(parseCitation(url.replace('localhost','evil.example')),null);
});
test('rename updates managed titles, manual edits remain conflicts',()=>{
 const rows=updatePlan([{text:'Old title',hyperlink:address},{text:'Human edit',hyperlink:address},{text:'Website',hyperlink:'https://example.com'}],catalog,origin);
 assert.equal(rows.length,2);assert.equal(rows[0].status,'update');assert.equal(rows[1].status,'manual');assert.equal(parseCitation(rows[0].nextAddress).citation,cid);
});

test('swapping assigned numbers uses stable material IDs and preserves locator decisions',()=>{
 const second='d'.repeat(32),firstAddress=citationUrl(origin,pid,did,'Annex 1, First','full',cid);
 const secondAddress=citationUrl(origin,pid,second,'Exhibit 2, Second','full','e'.repeat(32));
 const current={id:pid,documents:[{id:did,full:'Annex 2, First',short:'Annex 2, First'},{id:second,full:'Exhibit 1, Second',short:'Exhibit 1, Second'}]};
 const plan=updatePlan([{text:'Annex 1, First',hyperlink:firstAddress},{text:'Exhibit 2, Second',hyperlink:secondAddress}],current,origin);
 assert.equal(plan.length,2);assert.deepEqual(plan.map(p=>p.status),['update','update']);
 assert.deepEqual(plan.map(p=>parseCitation(p.nextAddress).document),[did,second]);
 assert.deepEqual(plan.map(p=>parseCitation(p.nextAddress).expected),['Annex 2, First','Exhibit 1, Second']);
 assert.equal(parseCitation(plan[0].nextAddress).citation,cid);
});
test('missing and foreign documents never receive automatic updates',()=>{
 assert.equal(updatePlan([{text:'Old title',hyperlink:address}],{...catalog,documents:[]},origin)[0].status,'missing');
 assert.equal(updatePlan([{text:'Old title',hyperlink:address}],{...catalog,id:'d'.repeat(32)},origin)[0].status,'other_project');
});
test('changed text, order or destination invalidates update plan',()=>{
 const old=[{text:'Old title',hyperlink:address}];assert.equal(verifyPlan(old,structuredClone(old)),true);
 assert.equal(verifyPlan(old,[{...old[0],text:'Edited'}]),false);assert.equal(verifyPlan(old,[]),false);
});

test('restored copy rebinds only exact known markers after selecting the copy',()=>{
 const copy={...catalog,id:'e'.repeat(32),restored_bindings:[parseCitation(address)]};
 const rows=updatePlan([{text:'Old title',hyperlink:address}],copy,origin);
 assert.equal(rows[0].status,'update');assert.equal(parseCitation(rows[0].nextAddress).project,copy.id);
 const foreign=citationUrl(origin,pid,did,'Old title','full','f'.repeat(32));
 assert.equal(updatePlan([{text:'Old title',hyperlink:foreign}],copy,origin)[0].status,'other_project');
 assert.equal(updatePlan([{text:'Manual edit',hyperlink:address}],copy,origin)[0].status,'manual');
});
