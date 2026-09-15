export function parseCitation(address) {
  try {
    const url = new URL(address);
    const match = /^\/api\/word\/open\/([a-f0-9]{32})\/([a-f0-9]{32})\/([a-f0-9]{32})$/.exec(url.pathname);
    const form = url.searchParams.get('form') || 'full', expected = url.searchParams.get('text');
    if (url.protocol !== 'https:' || !['localhost', '127.0.0.1'].includes(url.hostname) || !match || !['full', 'short'].includes(form) || !expected || expected.length > 1000) return null;
    return {project: match[1], document: match[2], citation: match[3], form, expected};
  } catch { return null; }
}

export function citationUrl(origin, project, document, text, form, citation) {
  return `${origin}/api/word/open/${project}/${document}/${citation}?${new URLSearchParams({form, text})}`;
}

export function updatePlan(links, catalog, origin) {
  const documents = new Map(catalog.documents.map(d => [d.id, d]));
  return links.flatMap((link, index) => {
    const meta = parseCitation(link.hyperlink);
    if (!meta) return [];
    const doc = documents.get(meta.document);
    const status = meta.project !== catalog.id ? 'other_project' : !doc ? 'missing' : link.text !== meta.expected ? 'manual' : doc[meta.form] !== link.text || new URL(link.hyperlink).origin !== origin ? 'update' : 'current';
    return [{index, old: link.text, next: doc?.[meta.form] || '', address: link.hyperlink, status,
      nextAddress: doc ? citationUrl(origin, catalog.id, doc.id, doc[meta.form], meta.form, meta.citation) : '', footnote: link.footnote}];
  });
}

export function verifyPlan(previous, current) {
  return JSON.stringify(previous) === JSON.stringify(current);
}
