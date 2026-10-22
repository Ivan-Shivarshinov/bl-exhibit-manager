import test from 'node:test';
import assert from 'node:assert/strict';
import {referenceDocuments} from '../src/referenceOrder.js';

test('reference options sort numeric identifiers, preserve IDs and original order', () => {
  const documents = [10,2,65,1,9].map(number=>({id:`id-${number}`,designation:'Annex',number,title:'Same',filename:`${number}.pdf`}));
  const saved = structuredClone(documents), selected = documents[1].id;
  assert.deepEqual(referenceDocuments(documents).map(d=>d.number),[1,2,9,10,65]);
  assert.equal(referenceDocuments(documents).find(d=>d.id===selected).number,2);
  assert.deepEqual(documents,saved);
});
test('groups use effective designation, natural prefixes, titles and stable ties', () => {
  const documents = [
    {id:'b',title:'Title 10',number:null},{id:'a',title:'Title 2'},
    {id:'c2',designation:'Exhibit',effective_format:{designation:'Annex'},prefix:'C2',number:3},
    {id:'c10',designation:'Annex',prefix:'C10',number:1},
    {id:'ex',designation:'Exhibit',number:1}, {id:'ann',designation:'Annex',number:10},
    {id:'z',title:'Title 2',filename:'A.pdf'},{id:'y',title:'Title 2',filename:'A.pdf'},
  ];
  const first=referenceDocuments(documents).map(d=>d.id);
  assert.deepEqual(first,['ann','c2','c10','ex','a','y','z','b']);
  assert.deepEqual(referenceDocuments([...documents].reverse()).map(d=>d.id),first);
});
