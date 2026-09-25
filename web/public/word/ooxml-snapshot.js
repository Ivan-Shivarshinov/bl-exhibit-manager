// Word's Flat OPC serialization is not byte-stable. Compare XML meaning,
// retaining document content and formatting resources, not export bookkeeping.
const PKG = 'http://schemas.microsoft.com/office/2006/xmlPackage';
const W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
const R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const REL = 'http://schemas.openxmlformats.org/package/2006/relationships';
const W14 = 'http://schemas.microsoft.com/office/word/2010/wordml';
const W15 = 'http://schemas.microsoft.com/office/word/2012/wordml';
const MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006';
const XMLNS = 'http://www.w3.org/2000/xmlns/';
const rsids = new Set(['rsidR', 'rsidRPr', 'rsidRDefault', 'rsidP', 'rsidDel', 'rsidTr', 'rsidSect']);
const children = node => Array.from(node.childNodes).filter(n => n.nodeType === 1);
const name = node => `{${node.namespaceURI || ''}}${node.localName}`;

export function ooxmlSnapshot(xml) {
  const invalid = () => { throw new Error('Не удалось проверить оформление Word. Документ не изменён.'); };
  let document;
  try { document = new DOMParser().parseFromString(xml, 'application/xml'); } catch { invalid(); }
  const root = document.documentElement;
  if (document.doctype || document.getElementsByTagName('parsererror').length ||
      root?.namespaceURI !== PKG || root.localName !== 'package') invalid();
  const parts = new Map();
  for (const part of children(root)) {
    const path = part.getAttributeNS(PKG, 'name');
    if (part.namespaceURI !== PKG || part.localName !== 'part' || !path?.startsWith('/') || parts.has(path)) invalid();
    parts.set(path, part);
  }
  if (!parts.has('/word/document.xml')) invalid();

  function canonical(node, relationships) {
    if (node.namespaceURI === W && ['rsids', 'proofErr'].includes(node.localName) ||
        [W14, W15].includes(node.namespaceURI) && node.localName === 'docId') return null;
    const attributes = Array.from(node.attributes).flatMap(attr => {
      if (attr.namespaceURI === XMLNS ||
          attr.namespaceURI === PKG && attr.localName === 'padding' ||
          attr.namespaceURI === W && rsids.has(attr.localName) ||
          attr.namespaceURI === W14 && ['paraId', 'textId'].includes(attr.localName)) return [];
      let value = attr.value;
      if (attr.namespaceURI === R && ['id', 'embed', 'link'].includes(attr.localName)) {
        if (!relationships.has(value)) invalid();
        value = relationships.get(value);
      } else if (attr.namespaceURI === MC && attr.localName === 'Ignorable') {
        value = value.trim().split(/\s+/).map(prefix => node.lookupNamespaceURI(prefix) || prefix).sort();
      }
      return [[name(attr), value]];
    }).sort((a, b) => a[0].localeCompare(b[0]));
    const content = [], hasElements = children(node).length > 0;
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 1) {
        const value = canonical(child, relationships);
        if (value !== null) content.push(value);
      } else if ([3, 4].includes(child.nodeType)) {
        // Whitespace in text-bearing elements (including w:t) is significant.
        if (!hasElements || child.nodeValue.trim()) {
          if (typeof content.at(-1) === 'string') content[content.length - 1] += child.nodeValue;
          else content.push(child.nodeValue);
        }
      }
    }
    return [name(node), attributes, content];
  }

  const snapshot = [];
  for (const [path, part] of parts) {
    // Root package properties do not describe the selected Word range.
    // Relationship IDs are compared through their actual targets below.
    if (!path.startsWith('/word/') || path.endsWith('.rels')) continue;
    const slash = path.lastIndexOf('/');
    const relpart = parts.get(`${path.slice(0, slash)}/_rels/${path.slice(slash + 1)}.rels`);
    const relationships = new Map();
    for (const rel of Array.from(relpart?.getElementsByTagNameNS(REL, 'Relationship') || [])) {
      const id = rel.getAttribute('Id');
      if (!id || relationships.has(id)) invalid();
      relationships.set(id, Array.from(rel.attributes).filter(a => a.name !== 'Id' && a.namespaceURI !== XMLNS)
        .map(a => [name(a), a.value]).sort((a, b) => a[0].localeCompare(b[0])));
    }
    snapshot.push([path, canonical(part, relationships)]);
  }
  return JSON.stringify(snapshot.sort((a, b) => a[0].localeCompare(b[0])));
}
