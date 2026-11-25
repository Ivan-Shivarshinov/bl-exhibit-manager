const original = /^(?:originals?|оригинал[ыи]?)$/i;
const translation = /^(?:translations?|перевод[ыи]?)$/i;

function describe(relativePath) {
  const segments = relativePath.normalize('NFC').toLocaleLowerCase('ru-RU').split('/');
  const stem = segments.pop().replace(/\.pdf$/i, '');
  const suffix = stem.match(/^(.+?)[ _.-]+(original|translation|оригинал|перевод)$/i);
  const folder = segments.at(-1);
  const folderRole = translation.test(folder) ? 'translation' : original.test(folder) ? 'original' : '';
  const suffixRole = suffix ? translation.test(suffix[2]) ? 'translation' : 'original' : '';
  if (folderRole && suffixRole && folderRole !== suffixRole) return null;
  if (folderRole) segments.pop();
  return {key: `${segments.join('/')}/${suffix ? suffix[1] : stem}`,
    role: suffixRole || folderRole || 'original', reason: folderRole ? 'Папки оригиналов и переводов, одинаковое имя PDF' : 'Одинаковая основа имени и пометка перевода'};
}

export function proposePairs(files) {
  const groups = new Map();
  for (const file of files) {
    const path = file.part?.relative_path;
    if (!path) continue;
    const item = describe(path);
    if (!item) continue;
    if (!groups.has(item.key)) groups.set(item.key, {original: [], translation: []});
    groups.get(item.key)[item.role].push({file, reason: item.reason});
  }
  return [...groups.values()].flatMap(group => {
    if (group.original.length !== 1 || group.translation.length !== 1) return [];
    const [{file: source}] = group.original;
    const [{file: target, reason}] = group.translation;
    return [{original: source, translation: target, reason}];
  });
}
