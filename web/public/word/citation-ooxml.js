// Insert the link and adjacent plain text together. Removing a hyperlink from
// an appended Word range can also remove the link it inherited from its neighbor.
const xml = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[ch]));

export function citationOoxml(text, identifier, address, before = '', after = '') {
  const run = (value, bold = false) => value ? `<w:r><w:rPr><w:b w:val="${bold ? 1 : 0}"/><w:color w:val="000000"/><w:u w:val="none"/></w:rPr><w:t xml:space="preserve">${xml(value)}</w:t></w:r>` : '';
  const hasIdentifier = identifier && (text === identifier || text.startsWith(identifier + ','));
  const title = hasIdentifier ? run(identifier, true) + run(text.slice(identifier.length)) : run(text);
  return `<?xml version="1.0" encoding="UTF-8"?>
<pkg:package xmlns:pkg="http://schemas.microsoft.com/office/2006/xmlPackage">
  <pkg:part pkg:name="/_rels/.rels" pkg:contentType="application/vnd.openxmlformats-package.relationships+xml"><pkg:xmlData><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rDoc" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships></pkg:xmlData></pkg:part>
  <pkg:part pkg:name="/word/document.xml" pkg:contentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"><pkg:xmlData><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p>${run(before)}<w:hyperlink r:id="rCitation">${title}</w:hyperlink>${run(after)}</w:p></w:body></w:document></pkg:xmlData></pkg:part>
  <pkg:part pkg:name="/word/_rels/document.xml.rels" pkg:contentType="application/vnd.openxmlformats-package.relationships+xml"><pkg:xmlData><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rCitation" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="${xml(address)}" TargetMode="External"/></Relationships></pkg:xmlData></pkg:part>
</pkg:package>`;
}
