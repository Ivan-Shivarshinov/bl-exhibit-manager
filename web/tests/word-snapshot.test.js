import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DOMParser} from '@xmldom/xmldom';
import {ooxmlSnapshot} from '../public/word/ooxml-snapshot.js';
import {citationOoxml} from '../public/word/citation-ooxml.js';

globalThis.DOMParser = DOMParser;
const w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
const base = citationOoxml('Annex 1, Old title', 'Annex 1', 'https://localhost:8778/old');
const part = (path, xml) => `<pkg:part pkg:name="${path}" pkg:contentType="application/xml"><pkg:xmlData>${xml}</pkg:xmlData></pkg:part>`;
const add = (xml, parts) => xml.replace('</pkg:package>', parts + '</pkg:package>');
const styles = `<w:styles xmlns:w="${w}"><w:style w:styleId="Title"><w:rPr><w:i/><w:rFonts w:ascii="Times New Roman"/></w:rPr></w:style></w:styles>`;

test('Word export bookkeeping, namespace prefixes, attribute and package order are not formatting edits', () => {
  const first = add(base.replace('<w:p>', '<w:p w:rsidR="11111111">'),
    part('/word/styles.xml', styles) + part('/word/settings.xml', `<w:settings xmlns:w="${w}"><w:rsids><w:rsid w:val="11111111"/></w:rsids><w14:docId xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" w14:val="old"/></w:settings>`));
  const second = add(base.replace('<w:p>', '<w:p xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" w:rsidR="99999999" w14:paraId="ABCD1234" w14:textId="1234ABCD">')
    .replaceAll('rCitation', 'rId72').replace('Id="rId72" Type=', 'TargetMode="External" Id="rId72" Type=').replace('TargetMode="External"/>', '/>'),
    part('/docProps/core.xml', '<metadata>new export time</metadata>') +
    part('/word/settings.xml', `<w:settings xmlns:w="${w}"><w:rsids><w:rsid w:val="99999999"/></w:rsids><w14:docId xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" w14:val="new"/></w:settings>`) + part('/word/styles.xml', styles))
    .replaceAll('w:', 'x:').replaceAll('xmlns:w=', 'xmlns:x=');
  assert.notEqual(first, second);
  assert.equal(ooxmlSnapshot(first), ooxmlSnapshot(second));
});

test('text, spaces, typography, inherited styles, relationship destinations and structure remain guarded', () => {
  const source = add(base, part('/word/styles.xml', styles));
  for (const changed of [
    source.replace('Old title', 'Edited title'),
    source.replace(', Old title', ',  Old title'),
    source.replace('w:val="1"', 'w:val="0"'),
    source.replace('000000', 'FF0000'),
    source.replace('<w:rPr>', '<w:rPr><w:i/>'),
    source.replace('Times New Roman', 'Arial'),
    source.replace('https://localhost:8778/old', 'https://localhost:8778/other'),
    source.replace('<w:p>', '<w:p><w:pPr><w:spacing w:line="480"/></w:pPr>'),
    source.replace('<w:p>', '<w:p><w:fldSimple w:instr="DATE"/>'),
  ]) assert.notEqual(ooxmlSnapshot(source), ooxmlSnapshot(changed));
});

test('theme resources and empty versus space-only Word text remain significant', () => {
  const theme = part('/word/theme/theme1.xml', '<a:theme xmlns:a="urn:theme"><a:font name="Arial"/></a:theme>');
  assert.notEqual(ooxmlSnapshot(add(base, theme)), ooxmlSnapshot(add(base, theme.replace('Arial', 'Cambria'))));
  assert.notEqual(ooxmlSnapshot(base.replace('Old title', '')), ooxmlSnapshot(base.replace('Old title', ' ')));
  assert.equal(ooxmlSnapshot(base), ooxmlSnapshot(base.replace('Old title', '<![CDATA[Old ]]>title')));
});

test('malformed, non-package, duplicate-part and unresolved-link XML fail closed', () => {
  for (const xml of [
    'not XML', '<w:document/>',
    base.replace('r:id="rCitation"', 'r:id="missing"'),
    add(base, part('/word/document.xml', '<wrong/>')),
  ]) assert.throws(() => ooxmlSnapshot(xml), /проверить оформление/);
});
