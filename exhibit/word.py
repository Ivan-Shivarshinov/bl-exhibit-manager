"""Surgical edits of footnotes.xml. All other package members remain byte-identical."""
from copy import deepcopy
from . import citations
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile
import re
from urllib.parse import quote
from lxml import etree as E

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W, "r": R}
FOOT = "word/footnotes.xml"
RELS = "word/_rels/footnotes.xml.rels"
RUN_ATOMS = {f'{{{W}}}{name}': value for name,value in (
    ('tab','\t'),('br','\n'),('cr','\n'),('noBreakHyphen','\u2011'),('softHyphen','\u00ad'))}
RUN_MARKERS = {f'{{{W}}}{name}' for name in ('footnoteRef','lastRenderedPageBreak')}
IDENT = re.compile(r"(?<![\w-])[A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)*-\d+(?![\w-])", re.I)
UNPREFIXED = re.compile(r"(?<![\w-])(?:Annex|Exhibit)\s+\d+(?![\w-])", re.I)
CASE_CONTEXT = re.compile(r'(?:\bcase\s*(?:no\.?|number|№)?|\bдел[аоу]?\s*(?:№|номер)?)\s*[:#]?\s*$', re.I)


def identifier_spans(text):
    """Do not interpret a fragment of a case number as an exhibit identifier."""
    for match in IDENT.finditer(text):
        a, b = match.span()
        if text[max(0,a-1):a] == '/' or text[b:b+1] == '/' or CASE_CONTEXT.search(text[:a]):
            continue
        yield a, b


def match_key(value):
    return ' '.join(value.split()).casefold()


def xml(data):
    return E.fromstring(data, E.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False))


def package(data):
    try:
        with ZipFile(BytesIO(data)) as z:
            if len(z.infolist()) > 5000 or sum(x.file_size for x in z.infolist()) > 100_000_000:
                raise ValueError("DOCX слишком большой после распаковки.")
            if len(set(z.namelist())) != len(z.namelist()):
                raise ValueError("В DOCX повторяются части пакета.")
            parts = {n: z.read(n) for n in z.namelist()}
        if "word/document.xml" not in parts or "[Content_Types].xml" not in parts:
            raise ValueError("Ожидается DOCX.")
        if xml(parts["word/document.xml"]).tag != f"{{{W}}}document":
            raise ValueError("Этот вариант OOXML пока не поддерживается. Нужен обычный Word DOCX (Transitional).")
        if any(n.startswith("_xmlsignatures/") or "vbaProject" in n for n in parts):
            raise ValueError("Подписанные документы и макросы не поддерживаются.")
        for n, content in parts.items():
            if n.endswith((".xml", ".rels")):
                root = xml(content)
                if root.getroottree().docinfo.doctype:
                    raise ValueError("DOCX с DTD не поддерживается.")
        return parts
    except (BadZipFile, E.XMLSyntaxError, KeyError) as exc:
        raise ValueError("Не удалось прочитать DOCX.") from exc


def text_of(node):
    out = []
    for x in node.iter():
        if x.tag == f"{{{W}}}t":
            out.append(x.text or "")
        elif x.tag in RUN_ATOMS:
            out.append(RUN_ATOMS[x.tag])
    return "".join(out)


def paragraphs(parts):
    if FOOT not in parts:
        return None, []
    root = xml(parts[FOOT])
    order = [x.get(f"{{{W}}}id") for x in xml(parts["word/document.xml"]).findall(".//w:footnoteReference", NS)]
    rows = []
    for foot in root.findall("w:footnote", NS):
        fid = foot.get(f"{{{W}}}id")
        if fid not in order:
            continue
        for index, p in enumerate(foot.findall(".//w:p", NS)):
            rows.append((fid, order.index(fid)+1, index, p))
    return root, rows


def scan(data, documents, saved=None, excluded=(), project_id=None):
    from .word_bridge import paragraph_links
    parts = package(data)
    _, paras = paragraphs(parts)
    saved = saved or {}
    result, footnotes = [], []
    for fid, ordinal, pi, p in paras:
        text = text_of(p)
        footnotes.append({"footnote": ordinal, "fid": fid, "paragraph": pi, "text": text})
        spans = set(identifier_spans(text)) | {m.span() for m in UNPREFIXED.finditer(text)}
        for d in documents:
            for name in [d.get("identifier", ""), d["title"], *d.get("aliases", [])]:
                if name.strip() and not name.strip().isdigit():
                    boundary = r"[\w/-]" if IDENT.fullmatch(name) else r"\w"
                    pattern=r'\s+'.join(re.escape(part) for part in name.split())
                    spans.update((m.start(), m.end()) for m in re.finditer(r"(?<!"+boundary+")"+pattern+r"(?!"+boundary+")", text, re.I))
        # Exclusions apply to this occurrence, not to the document's text.
        blocked = [(r['start'],r['end']) for r in excluded if r['fid']==fid and r['paragraph']==pi and text[r['start']:r['end']]==r['mention']]
        spans = {(a,b) for a,b in spans if not any(a < y and b > x for x,y in blocked)}
        # Prefer the longer mention when a title contains its identifier.
        managed = [r for r in paragraph_links(parts, p, project_id) if not any(r['start'] < y and r['end'] > x for x,y in blocked)]
        managed_by_span = {(r['start'], r['end']): r for r in managed}
        chosen = list(managed_by_span)
        chosen += [(r['start'],r['end']) for r in saved.values() if r.get('custom') and r['fid']==fid and r['paragraph']==pi and text[r['start']:r['end']]==r['mention'] and not any(r['start'] < b and r['end'] > a for a,b in chosen)]
        for a, b in sorted(spans, key=lambda s: (-(s[1]-s[0]), s[0])):
            if not any(a < y and b > x for x, y in chosen):
                chosen.append((a, b))
        paragraph_refs=[]
        for a, b in sorted(chosen):
            mention = text[a:b]
            candidates = [d["id"] for d in documents if match_key(mention) in
                          [match_key(str(d.get("identifier", ""))), match_key(d["title"]),
                           *[match_key(x) for x in d.get("aliases", [])]]]
            key = f"{fid}:{pi}:{a}:{b}"
            if (a, b) in managed_by_span:
                marker = managed_by_span[(a, b)]
                target = marker['target'] if any(d['id'] == marker['target'] for d in documents) else None
                previous = saved.get(key)
                manual = bool(previous and previous.get('manual') and previous.get('mention') == mention)
                if manual: target = previous.get('target')
                paragraph_refs.append({**marker, 'target': target, 'key': key, 'fid': fid, 'footnote': ordinal,
                                       'paragraph': pi, 'candidates': [target] if target else [], 'manual': manual,
                                       'keep_original': bool(manual and previous.get('keep_original'))})
                continue
            prev = saved.get(key)
            target = prev.get("target") if prev and prev.get("mention") == mention else (candidates[0] if len(candidates) == 1 else None)
            paragraph_refs.append({"key": key, "fid": fid, "footnote": ordinal, "paragraph": pi, "start": a, "end": b,
                           "mention": mention, "target": target, "candidates": candidates,
                           "manual": bool(prev and prev.get("manual")),
                           "keep_original": bool(prev and prev.get('mention') == mention and prev.get('keep_original')),
                           **({'custom':True} if prev and prev.get('custom') else {})})
        # Identifier followed by this same document's known title is one citation.
        # Keep the identifier anchor stable for saved choices and range edits.
        consolidated=[]
        by_id={d['id']:d for d in documents}
        for ref in paragraph_refs:
            prior=consolidated[-1] if consolidated else None
            if prior and not prior.get('managed') and not ref.get('managed') and prior['target'] and prior['target']==ref['target'] and not ref.get('custom') and not saved.get(ref['key'],{}).get('scope_manual'):
                doc=by_id.get(prior['target'],{})
                between=text[prior['end']:ref['start']]
                if match_key(prior['mention'])==match_key(doc.get('identifier','')) and match_key(ref['mention'])!=match_key(doc.get('identifier','')) and re.match(r'\s*,',between) and ';' not in between and not citations.LOCATOR.search(between):
                    continue
            consolidated.append(ref)
        paragraph_refs=consolidated
        citations.propose(text,paragraph_refs)
        for ref in paragraph_refs:
            if ref.get('managed'):
                ref.update(link_start=ref['start'], link_end=ref['end'], link_text=ref['mention'],
                           scope_review=ref['mention'] != ref['managed']['expected'])
                continue
            prev=saved.get(ref['key'])
            if prev and prev.get('scope_manual') and prev['mention']==ref['mention']:
                ref.update({k:prev[k] for k in ('link_start','link_end','link_text','scope_manual')})
                ref['scope_review']=False
        result.extend(paragraph_refs)
    return {"references": result, "footnotes": footnotes}


def run_piece(run, start, end):
    clone = deepcopy(run)
    for child in list(clone):
        if child.tag != f"{{{W}}}rPr":
            clone.remove(child)
    pos = 0
    for child in run:
        if child.tag == f'{{{W}}}rPr': continue
        length = len(text_of(child))
        a,b = max(0,start-pos),min(length,end-pos)
        if a < b:
            piece = deepcopy(child)
            if child.tag == f'{{{W}}}t':
                piece.text = (child.text or '')[a:b]
                piece.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
            clone.append(piece)
        pos += length
    return clone


def split_layout_runs(p, start, end):
    """Isolate Word's layout atoms; never turn breaks/tabs into ordinary text."""
    allowed = {f'{{{W}}}rPr',f'{{{W}}}t'} | RUN_ATOMS.keys() | RUN_MARKERS
    pos = 0
    for child in list(p):
        length = len(text_of(child))
        if pos < end and pos+length > start:
            if child.tag == f'{{{W}}}hyperlink':
                split_layout_runs(child,max(0,start-pos),min(length,end-pos))
            elif child.tag == f'{{{W}}}r':
                unsupported = [x for x in child if x.tag not in allowed]
                if unsupported:
                    names = ', '.join(sorted({E.QName(x).localname for x in unsupported}))
                    raise ValueError(f'Неподдерживаемая структура внутри упоминания ({names}); DOCX не изменён.')
                if any(x.tag in RUN_ATOMS or x.tag in RUN_MARKERS for x in child):
                    index = p.index(child)
                    for atom in child:
                        if atom.tag == f'{{{W}}}rPr': continue
                        run = deepcopy(child)
                        run[:] = [deepcopy(x) for x in child if x.tag == f'{{{W}}}rPr']
                        run.append(deepcopy(atom))
                        p.insert(index,run);index += 1
                    p.remove(child)
        pos += length


def supported_run(run):
    return run.tag == f'{{{W}}}r' and all(x.tag in ({f'{{{W}}}rPr',f'{{{W}}}t'} | RUN_ATOMS.keys()) for x in run)


def neutral_link_style(run, bold=False):
    """Keep source typography; only the exhibit designation receives bold."""
    props=run.find('w:rPr',NS)
    if props is None:
        props=E.Element(f'{{{W}}}rPr');run.insert(0,props)
    order='rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof snapToGrid vanish webHidden color spacing w kern position sz szCs highlight u effect bdr shd fitText vertAlign rtl cs em lang eastAsianLayout specVanish oMath rPrChange'.split()
    for name,value in [('color','000000'),('u','none')] + ([('b','1'),('bCs','1')] if bold else []):
        for old in props.findall('w:'+name,NS):props.remove(old)
        element=E.Element(f'{{{W}}}{name}',{f'{{{W}}}val':value})
        index=next((i for i,child in enumerate(props) if E.QName(child).localname in order and order.index(E.QName(child).localname)>order.index(name)),len(props))
        props.insert(index,element)


def wrap(p, start, end, rid, bold_span=None):
    # Complex fields / revisions require a later, separately verified OOXML implementation.
    if p.xpath(".//w:fldChar | .//w:fldSimple | .//w:ins | .//w:del | .//w:sdt", namespaces=NS):
        raise ValueError("Ссылка находится в поле, исправлении или элементе управления Word; требуется ручная обработка этой сноски.")
    split_layout_runs(p,start,end)
    pos = 0
    for child in list(p):
        length = len(text_of(child))
        if child.tag == f"{{{W}}}hyperlink" and pos < end and pos+length > start:
            index = p.index(child)
            # Keep the old hyperlink on text outside the replacement span.
            groups = [[], [], []]
            cursor = pos
            for part in child:
                size = len(text_of(part))
                if size == 0:
                    groups[0 if cursor < start else 2 if cursor >= end else 1].append(deepcopy(part))
                elif cursor >= end or cursor + size <= start:
                    groups[0 if cursor + size <= start else 2].append(deepcopy(part))
                else:
                    if not supported_run(part):
                        raise ValueError("Неподдерживаемая структура существующей ссылки; DOCX не изменён.")
                    a, b = max(0, start-cursor), min(size, end-cursor)
                    if a: groups[0].append(run_piece(part, 0, a))
                    groups[1].append(run_piece(part, a, b))
                    if b < size: groups[2].append(run_piece(part, b, size))
                cursor += size
            replacements = []
            for group_index, group in enumerate(groups):
                if not group: continue
                if group_index == 1:
                    replacements.extend(group)
                else:
                    preserved = deepcopy(child)
                    preserved[:] = group
                    replacements.append(preserved)
            for part in replacements:
                p.insert(index, part)
                index += 1
            p.remove(child)
        pos += length
    pos, selected = 0, []
    for child in list(p):
        length = len(text_of(child))
        a, b = max(start-pos, 0), min(end-pos, length)
        if a < b:
            if not supported_run(child):
                raise ValueError("Неподдерживаемая структура внутри упоминания; DOCX не изменён.")
            index = p.index(child)
            pieces = []
            if a:
                pieces.append(run_piece(child, 0, a))
            cuts = sorted({a,b} | {point-pos for point in (bold_span or ()) if a < point-pos < b})
            mids = [run_piece(child,x,y) for x,y in zip(cuts,cuts[1:])]
            for mid,x,y in zip(mids,cuts,cuts[1:]):
                neutral_link_style(mid, bool(bold_span and bold_span[0] <= pos+x and pos+y <= bold_span[1]))
            pieces.extend(mids)
            if b < length:
                pieces.append(run_piece(child, b, length))
            for offset, piece in enumerate(pieces):
                p.insert(index+offset, piece)
            p.remove(child)
            selected.extend(mids)
        pos += length
    if not selected:
        raise ValueError("Упоминание изменилось. Повторите сопоставление.")
    first, last = p.index(selected[0]), p.index(selected[-1])
    middle = list(p)[first:last+1]
    if any(x.tag not in (f"{{{W}}}r", f"{{{W}}}bookmarkStart", f"{{{W}}}bookmarkEnd", f"{{{W}}}proofErr") for x in middle):
        raise ValueError("Сложная структура между частями упоминания; требуется ручная проверка.")
    link = E.Element(f"{{{W}}}hyperlink", {f"{{{R}}}id": rid})
    p.insert(first, link)
    for child in middle:
        if child.tag==f'{{{W}}}r':neutral_link_style(child)
        link.append(child)


def add_links(data, references, paths):
    parts = package(data)
    root, rows = paragraphs(parts)
    if not any(not r.get('keep_original') for r in references):
        return data
    rels = xml(parts[RELS]) if RELS in parts else E.Element(f"{{{REL}}}Relationships", nsmap={None: REL})
    existing = {x.get("Target"): x.get("Id") for x in rels if x.get("Type", "").endswith("/hyperlink") and x.get("TargetMode") == "External"}
    ids = {x.get("Id") for x in rels}
    for fid, ordinal, pi, p in rows:
        refs = [r for r in references if r["fid"] == fid and r["paragraph"] == pi]
        citations.validate(text_of(p),refs)
        for ref in sorted((r for r in refs if not r.get('keep_original')), key=lambda r: citations.bounds(r)[0], reverse=True):
            if text_of(p)[ref["start"]:ref["end"]] != ref["mention"]:
                raise ValueError("Текст сноски изменился. Повторите сопоставление.")
            target = quote(paths[ref["target"]], safe="/")
            rid = existing.get(target)
            if not rid:
                i = 1
                while f"rIdExhibit{i}" in ids:
                    i += 1
                rid = f"rIdExhibit{i}"
                ids.add(rid)
                E.SubElement(rels, f"{{{REL}}}Relationship", Id=rid, Type=R+"/hyperlink", Target=target, TargetMode="External")
                existing[target] = rid
            try:
                start,end = citations.bounds(ref)
                label = UNPREFIXED.match(text_of(p), start) or IDENT.match(text_of(p), start)
                bold_span = label.span() if label and label.end() <= end else None
                wrap(p, start, end, rid, bold_span)
            except ValueError as exc:
                raise ValueError(f"Сноска {ordinal}: {exc}") from exc
    from .word_bridge import parse_link
    used = {node.get(f'{{{R}}}id') for node in root.findall('.//w:hyperlink', NS)}
    for rel in list(rels):
        if rel.get('Id') not in used and parse_link(rel.get('Target', '')):
            rels.remove(rel)
    parts[FOOT] = E.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    parts[RELS] = E.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        for name, value in parts.items():
            z.writestr(name, value)
    return out.getvalue()
