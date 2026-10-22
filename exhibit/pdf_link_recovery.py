"""Recover missing annotations only from an exact, tagged, single-page footnote.

No page text is changed. Untagged, ambiguous, renumbered or split notes fail
closed through the normal marker completeness check in main_pdf.
"""
from collections import defaultdict
from contextlib import closing
from io import BytesIO
import unicodedata

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from pypdf.generic import ArrayObject, NameObject

from . import word
from .pdf import PDF_LOCK


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text) if not c.isspace())


def source_notes(data, markers):
    parts = word.package(data)
    if word.FOOT not in parts or word.RELS not in parts:
        return {}
    body = word.xml(parts['word/document.xml'])
    # Recovery deliberately supports only continuous default decimal numbering.
    # Other numbering schemes still export normally when no annotation is lost.
    settings = word.xml(parts['word/settings.xml']) if 'word/settings.xml' in parts else body
    for root in (body, settings):
        for props in root.findall('.//w:footnotePr', word.NS):
            for name, allowed in [('numFmt', 'decimal'), ('numStart', '1'), ('numRestart', 'continuous')]:
                value = props.find('w:'+name, word.NS)
                if value is not None and value.get('{'+word.W+'}val') != allowed:
                    return {}
    refs = body.findall('.//w:footnoteReference', word.NS)
    ids = [r.get('{'+word.W+'}id') for r in refs]
    if len(set(ids)) != len(ids) or any(r.get('{'+word.W+'}customMarkFollows') for r in refs):
        return {}
    if any(a.tag in ('{'+word.W+'}del', '{'+word.W+'}moveFrom') for r in refs for a in r.iterancestors()):
        return {}
    numbers = {fid: str(i+1) for i, fid in enumerate(ids)}
    rels = {r.get('Id'): r.get('Target') for r in word.xml(parts[word.RELS])}
    result = {}
    for note in word.xml(parts[word.FOOT]):
        fid = note.get('{'+word.W+'}id')
        if fid not in numbers:
            continue
        # Field results / hidden text / drawings need additional rendering proof.
        if any(note.findall('.//w:'+tag, word.NS) for tag in ('fldChar', 'instrText', 'vanish', 'drawing', 'pict', 'del', 'moveFrom')):
            continue
        text = ''; ranges = []
        for node in note.iter():
            if node.tag == '{'+word.W+'}hyperlink':
                marker = rels.get(node.get('{'+word.R+'}id'))
                if marker in markers:
                    link_text = normalized(''.join(node.xpath('.//w:t/text()', namespaces=word.NS)))
                    ranges.append((marker, len(text), len(text)+len(link_text)))
            elif node.tag == '{'+word.W+'}t':
                text += normalized(node.text or '')
        for marker, start, end in ranges:
            if start < end:
                result[marker] = {'number': numbers[fid], 'text': text, 'start': start, 'end': end}
    return result


def tagged_notes(reader):
    """Return rendered Note text, including its label, keyed by actual page."""
    pages = {p.indirect_reference.idnum: i for i, p in enumerate(reader.pages)}
    marks = {}; visited = set(); invalid = set()

    def visit(value, page=None, note=None):
        if hasattr(value, 'idnum'):
            if value.idnum in visited:
                return
            visited.add(value.idnum)
        value = value.get_object() if hasattr(value, 'get_object') else value
        if isinstance(value, list):
            for item in value:
                visit(item, page, note)
        elif isinstance(value, dict):
            if '/Pg' in value:
                page = pages.get(value['/Pg'].indirect_reference.idnum)
            if value.get('/S') == '/Note':
                note = id(value)
            if '/MCID' in value:
                visit(value['/MCID'], page, note)
            if '/K' in value:
                visit(value['/K'], page, note)
        elif isinstance(value, int) and note is not None and page is not None:
            key = (page, int(value))
            if key in marks and marks[key] != note:
                invalid.update((note, marks[key]))
            marks[key] = note

    struct = reader.trailer['/Root'].get('/StructTreeRoot')
    if not struct:
        return []
    visit(struct)
    texts = defaultdict(list); locations = defaultdict(set)
    for pi, page in enumerate(reader.pages):
        stack = []

        def before(op, args, cm, tm):
            if op == b'BDC':
                stack.append(args[1].get('/MCID') if isinstance(args[1], dict) else None)
            elif op == b'BMC':
                stack.append(None)
            elif op == b'EMC' and stack:
                stack.pop()

        def text(value, cm, tm, font, size):
            note = marks.get((pi, stack[-1])) if stack else None
            if note is not None and value.strip():
                texts[note].append(value); locations[note].add(pi)

        page.extract_text(visitor_operand_before=before, visitor_text=text)
    return [(next(iter(locations[n])), normalized(''.join(t))) for n, t in texts.items()
            if len(locations[n]) == 1 and n not in invalid]


def recover(data, markers, source):
    reader = PdfReader(BytesIO(data))
    seen = set()
    for page in reader.pages:
        for ref in page.get('/Annots', []):
            action = ref.get_object().get('/A')
            if action:
                seen.add(str(action.get_object().get('/URI', '')))
    missing = set(markers) - seen
    if not missing:
        return data
    sources = source_notes(source, markers)
    candidates = tagged_notes(reader)
    if not sources or not candidates:
        return data
    # A vertical edge overlap can be a superscript's annotation padding. Require
    # the text's vertical centre to be covered before treating it as a conflict.
    def covers_text(rect, box):
        l, b, r, t = map(float, rect)
        return box and float(box[1]) < (b+t)/2 < float(box[3]) and max(l, float(box[0])) < min(r, float(box[2]))

    planned = {}
    with PDF_LOCK, pdfium.PdfDocument(data) as pdf:
        cached = {}

        def regions(marker):
            note = sources.get(marker)
            if not note:
                return None
            expected = note['number']+note['text']
            matches = [pi for pi, text in candidates if text == expected]
            if len(matches) != 1:
                return None
            pi = matches[0]; page = reader.pages[pi]
            if page.rotation or list(page.mediabox) != list(page.cropbox) or list(page.mediabox)[:2] != [0, 0]:
                return None
            if pi not in cached:
                with closing(pdf[pi]) as pdfpage, closing(pdfpage.get_textpage()) as textpage:
                    chars = []; indices = []
                    for i in range(textpage.count_chars()):
                        text = normalized(textpage.get_text_range(i, 1))
                        chars.append(text); indices.extend([i]*len(text))
                    cached[pi] = (''.join(chars), indices)
            text, indices = cached[pi]
            at = text.find(expected)
            if at < 0 or text.find(expected, at+1) >= 0:
                return None
            start = at+len(note['number'])+note['start']; end = at+len(note['number'])+note['end']
            first, last = indices[start], indices[end-1]
            with closing(pdf[pi]) as pdfpage, closing(pdfpage.get_textpage()) as textpage:
                if normalized(textpage.get_text_range(first, last-first+1)) != note['text'][note['start']:note['end']]:
                    return None
                rectangles = [textpage.get_rect(i) for i in range(textpage.count_rects(first, last-first+1))]
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            if not rectangles or any(not (0 <= l < r <= width and 0 <= b < t <= height) for l,b,r,t in rectangles):
                return None
            return pi, rectangles

        pending = sorted(missing)
        while pending:
            marker = pending.pop()
            if marker in planned:
                continue
            found = regions(marker)
            if not found:
                if marker in missing:
                    continue  # Final completeness check still blocks partial recovery.
                return data  # Cannot safely move a conflicting existing annotation.
            planned[marker] = found
            pi, rectangles = found
            for ref in reader.pages[pi].get('/Annots', []):
                annotation = ref.get_object()
                if annotation.get('/Subtype') != '/Link':
                    continue
                action = annotation.get('/A')
                other = str(action.get_object().get('/URI', '')) if action else ''
                if other == marker:
                    continue
                if any(covers_text(rect, annotation.get('/Rect')) for rect in rectangles):
                    if other not in markers:
                        return data
                    if other not in planned:
                        pending.append(other)

    # Proofs for two different targets must not compete for the same text area.
    for marker, (pi, rects) in planned.items():
        for other, (pj, boxes) in planned.items():
            if marker != other and pi == pj and any(covers_text(r, b) for r in rects for b in boxes):
                return data
    if not planned: return data
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        if '/Annots' not in page: continue
        kept = []
        for ref in page['/Annots']:
            action = ref.get_object().get('/A')
            uri = str(action.get_object().get('/URI', '')) if action else ''
            if uri not in planned: kept.append(ref)
        page[NameObject('/Annots')] = ArrayObject(kept)
    for marker, (pi, rectangles) in planned.items():
        for rect in rectangles:
            writer.add_annotation(pi, Link(rect=rect, url=marker))
    out = BytesIO(); writer.write(out)
    return out.getvalue()
