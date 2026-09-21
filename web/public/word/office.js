import {citationUrl, parseCitation, updatePlan, verifyPlan} from './core.js';
import {citationOoxml} from './citation-ooxml.js';

async function editable(context) {
  context.document.load('changeTrackingMode');
  await context.sync();
  if (context.document.changeTrackingMode !== 'Off') throw new Error('Перед вставкой или обновлением отключите запись исправлений в Word. Существующие исправления автоматически не принимаются.');
}

export async function insertCitation(catalog, documentId, form, pinpoint, existing) {
  const doc = catalog.documents.find(d => d.id === documentId);
  if (!doc) throw new Error('Выберите приложение.');
  return Word.run(async context => {
    await editable(context);
    const selection = context.document.getSelection();
    selection.parentBody.load('type,text'); await context.sync();
    let position, before = '';
    if (existing) {
      // A selection inside a note can report the generic NoteItem body type.
      // Find its actual footnote, rather than accepting endnotes or the whole story.
      const notes = context.document.body.footnotes;
      notes.load('items'); await context.sync();
      const locations = notes.items.map(note => selection.compareLocationWith(note.body.getRange()));
      await context.sync();
      const index = locations.findIndex(result => ['Equal', 'Inside', 'InsideStart', 'InsideEnd'].includes(result.value));
      if (index < 0) throw new Error('Поставьте курсор в текст существующей сноски внизу страницы.');
      const body = notes.items[index].body;
      body.load('text'); await context.sync();
      position = body.getRange('End');
      if (body.text.trim()) {
        before = catalog.style.separator;
      }
    } else {
      if (selection.parentBody.type !== 'MainDoc') throw new Error('Для новой сноски поставьте курсор в основной текст документа.');
      const note = selection.insertFootnote('');
      position = note.body.getRange('End');
    }
    const address = citationUrl(location.origin, catalog.id, doc.id, doc[form], form, crypto.randomUUID().replaceAll('-', ''));
    const after = pinpoint.trim() ? catalog.style.locator_separator + pinpoint.trim() : '';
    position.insertOoxml(citationOoxml(doc[form], doc.identifier, address, before, after), 'After');
    await context.sync();
  });
}

async function references(context) {
  const notes = context.document.body.footnotes;
  notes.load('items'); await context.sync();
  const collections = notes.items.map(note => note.body.getRange().getHyperlinkRanges());
  collections.forEach(c => c.load('items/text,items/hyperlink'));
  await context.sync();
  const ranges = [], links = [];
  collections.forEach((collection, i) => collection.items.forEach(range => {
    ranges.push(range); links.push({text: range.text, hyperlink: range.hyperlink, footnote: i+1});
  }));
  return {ranges, links};
}

export async function inspectUpdates(catalog) {
  return Word.run(async context => {
    const {links} = await references(context);
    return {catalogVersion: catalog.version, links, rows: updatePlan(links, catalog, location.origin)};
  });
}

export async function applyUpdates(catalog, plan, indices) {
  if (catalog.version !== plan.catalogVersion) throw new Error('Приложения изменились. Повторите проверку ссылок.');
  return Word.run(async context => {
    await editable(context);
    const {ranges, links} = await references(context);
    if (!verifyPlan(plan.links, links)) throw new Error('Текст Word изменился после проверки. Повторите проверку ссылок.');
    const selected = plan.rows.filter(row => indices.includes(row.index));
    if (selected.some(row => row.status !== 'update')) throw new Error('Ручные изменения нельзя перезаписать автоматически.');
    // All reads and validation precede mutations. Only the linked range is replaced.
    for (const row of selected.reverse()) {
      const doc = catalog.documents.find(d => d.id === parseCitation(row.nextAddress)?.document);
      ranges[row.index].insertOoxml(citationOoxml(row.next, doc?.identifier || '', row.nextAddress), 'Replace');
    }
    await context.sync();
    return selected.length;
  });
}

export async function currentDocx() {
  const file = await new Promise((resolve, reject) => Office.context.document.getFileAsync(Office.FileType.Compressed, {sliceSize: 65536}, result => result.status === Office.AsyncResultStatus.Succeeded ? resolve(result.value) : reject(new Error(result.error.message))));
  try {
    if (file.size > 50000000) throw new Error('Основной DOCX превышает 50 МБ.');
    const slices = [];
    for (let i = 0; i < file.sliceCount; i++) {
      const slice = await new Promise((resolve, reject) => file.getSliceAsync(i, result => result.status === Office.AsyncResultStatus.Succeeded ? resolve(result.value) : reject(new Error(result.error.message))));
      slices.push(new Uint8Array(slice.data));
    }
    return new Blob(slices, {type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'});
  } finally {
    await new Promise(resolve => file.closeAsync(resolve));
  }
}
