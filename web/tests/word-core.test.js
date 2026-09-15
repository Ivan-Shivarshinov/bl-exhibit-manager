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
test('missing and foreign documents never receive automatic updates',()=>{
 assert.equal(updatePlan([{text:'Old title',hyperlink:address}],{...catalog,documents:[]},origin)[0].status,'missing');
 assert.equal(updatePlan([{text:'Old title',hyperlink:address}],{...catalog,id:'d'.repeat(32)},origin)[0].status,'other_project');
});
test('changed text, order or destination invalidates update plan',()=>{
 const old=[{text:'Old title',hyperlink:address}];assert.equal(verifyPlan(old,structuredClone(old)),true);
 assert.equal(verifyPlan(old,[{...old[0],text:'Edited'}]),false);assert.equal(verifyPlan(old,[]),false);
});
