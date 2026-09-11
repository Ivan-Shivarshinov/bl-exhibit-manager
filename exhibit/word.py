"""Surgical edits of footnotes.xml. All other package members remain byte-identical."""
from copy import deepcopy
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
IDENT = re.compile(r"(?<![\w-])[A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)*-\d+(?![\w-])", re.I)


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
        elif x.tag == f"{{{W}}}tab":
            out.append("\t")
        elif x.tag in (f"{{{W}}}br", f"{{{W}}}cr"):
            out.append("\n")
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


def scan(data, documents, saved=None):
    parts = package(data)
    _, paras = paragraphs(parts)
    saved = saved or {}
    result, footnotes = [], []
    for fid, ordinal, pi, p in paras:
        text = text_of(p)
        footnotes.append({"footnote": ordinal, "fid": fid, "paragraph": pi, "text": text})
        spans = {(m.start(), m.end()) for m in IDENT.finditer(text)}
        for d in documents:
            for name in [d.get("identifier", ""), d["title"], *d.get("aliases", [])]:
                if name.strip():
                    boundary = r"[\w-]" if IDENT.fullmatch(name) else r"\w"
                    spans.update((m.start(), m.end()) for m in re.finditer(r"(?<!"+boundary+")"+re.escape(name)+r"(?!"+boundary+")", text, re.I))
        # Prefer the longer mention when a title contains its identifier.
        chosen = []
        for a, b in sorted(spans, key=lambda s: (-(s[1]-s[0]), s[0])):
            if not any(a < y and b > x for x, y in chosen):
                chosen.append((a, b))
        for a, b in sorted(chosen):
            mention = text[a:b]
            candidates = [d["id"] for d in documents if mention.casefold() in
                          [str(d.get("identifier", "")).casefold(), d["title"].casefold(),
                           *[x.casefold() for x in d.get("aliases", [])]]]
            key = f"{fid}:{pi}:{a}:{b}"
            prev = saved.get(key)
            target = prev.get("target") if prev and prev.get("mention") == mention else (candidates[0] if len(candidates) == 1 else None)
            result.append({"key": key, "fid": fid, "footnote": ordinal, "paragraph": pi, "start": a, "end": b,
                           "mention": mention, "target": target, "candidates": candidates,
                           "manual": bool(prev and prev.get("manual"))})
    return {"references": result, "footnotes": footnotes}


def run_piece(run, start, end):
    clone = deepcopy(run)
    for child in list(clone):
        if child.tag != f"{{{W}}}rPr":
            clone.remove(child)
    t = E.SubElement(clone, f"{{{W}}}t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text_of(run)[start:end]
    return clone


def wrap(p, start, end, rid):
    # Complex fields / revisions require a later, separately verified OOXML implementation.
    if p.xpath(".//w:fldChar | .//w:fldSimple | .//w:ins | .//w:del | .//w:sdt", namespaces=NS):
        raise ValueError("Ссылка находится в поле, исправлении или элементе управления Word; требуется ручная обработка этой сноски.")
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
                    if part.tag != f"{{{W}}}r" or any(x.tag not in (f"{{{W}}}rPr", f"{{{W}}}t") for x in part):
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
            if child.tag != f"{{{W}}}r" or any(x.tag not in (f"{{{W}}}rPr", f"{{{W}}}t") for x in child):
                raise ValueError("Неподдерживаемая структура внутри упоминания; DOCX не изменён.")
            index = p.index(child)
            pieces = []
            if a:
                pieces.append(run_piece(child, 0, a))
            mid = run_piece(child, a, b)
            pieces.append(mid)
            if b < length:
                pieces.append(run_piece(child, b, length))
            for offset, piece in enumerate(pieces):
                p.insert(index+offset, piece)
            p.remove(child)
            selected.append(mid)
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
        link.append(child)


def add_links(data, references, paths):
    parts = package(data)
    root, rows = paragraphs(parts)
    if not references:
        return data
    rels = xml(parts[RELS]) if RELS in parts else E.Element(f"{{{REL}}}Relationships", nsmap={None: REL})
    existing = {x.get("Target"): x.get("Id") for x in rels if x.get("Type", "").endswith("/hyperlink") and x.get("TargetMode") == "External"}
    ids = {x.get("Id") for x in rels}
    for fid, _, pi, p in rows:
        refs = [r for r in references if r["fid"] == fid and r["paragraph"] == pi]
        for ref in sorted(refs, key=lambda r: r["start"], reverse=True):
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
            wrap(p, ref["start"], ref["end"], rid)
    parts[FOOT] = E.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    parts[RELS] = E.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        for name, value in parts.items():
            z.writestr(name, value)
    return out.getvalue()
