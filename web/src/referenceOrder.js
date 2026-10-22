const natural = new Intl.Collator('ru', {numeric: true, sensitivity: 'base'});
const text = value => String(value ?? '');
const compareText = (a, b) => natural.compare(text(a), text(b));

// Only the options are sorted. Target IDs and the stored document order stay intact.
export function referenceDocuments(documents) {
  return [...documents].sort((a, b) => {
    const numberedA = a.number !== null && a.number !== undefined;
    const numberedB = b.number !== null && b.number !== undefined;
    if (numberedA !== numberedB) return numberedA ? -1 : 1;
    if (numberedA) {
      const group = compareText(a.effective_format?.designation ?? a.designation, b.effective_format?.designation ?? b.designation)
        || compareText(a.prefix, b.prefix);
      if (group) return group;
      if (a.number !== b.number) return a.number - b.number;
    }
    return compareText(a.title, b.title) || compareText(a.filename, b.filename)
      || (text(a.id) < text(b.id) ? -1 : text(a.id) > text(b.id) ? 1 : 0);
  });
}
