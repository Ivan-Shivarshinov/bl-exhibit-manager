from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from threading import RLock
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED
import json
import os
import re
import unicodedata

from . import pdf, word, main_pdf, citations
from .identifiers import identifier as document_identifier

LOCK = RLock()
ROOT = Path(os.environ.get("EXHIBIT_DATA_DIR", Path.home() / ".bl-exhibit-manager"))
DEFAULT_STYLE = {"font": "DejaVu", "size": 10, "margin": 24}
LEGACY_LAYOUT = {'stamp_mode':'band','top':26}
NEW_LAYOUT = {'stamp_mode':'overlay','top':26}
DEFAULT_LABELS = {"designation": "Exhibit", "original_label": "[Original]", "translation_label": "[Translation]"}
FORMAT_KEYS = set(DEFAULT_STYLE) | set(DEFAULT_LABELS) | set(LEGACY_LAYOUT)
EDITABLE = {"title", "prefix", "number", "designation", "filename", "folder", "language", "mode",
            "original_label", "translation_label", "selection", "translation_selection", "aliases", "style", "format_overrides", "filename_mode", "short_title"}


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def identifier(doc, p=None):
    designation = effective_format(p,doc)[0]['designation'] if p is not None else doc.get('effective_format',doc).get('designation','')
    return document_identifier({**doc,'designation':designation})


def review_digest(doc, style):
    return digest({"doc": {k: v for k, v in doc.items() if k not in ("approved", "identifier", "format_overrides", "filename_mode", "short_title")}, "style": style})


def group_key(folder):
    return unicodedata.normalize("NFC", folder).casefold()


def overrides(doc):
    # Older projects keep their explicitly saved labels and individual font settings.
    return deepcopy(doc.get("format_overrides", {**{k: doc[k] for k in DEFAULT_LABELS}, **(doc.get("style") or {})}))


def effective_format(p, doc):
    values = {**LEGACY_LAYOUT, **DEFAULT_LABELS, **p.get("format", {}), **p["style"]}
    sources = {k: "project" for k in FORMAT_KEYS}
    for name, layer in [("group", p.get("group_styles", {}).get(group_key(doc["folder"]), {})), ("document", overrides(doc))]:
        values.update(layer)
        sources.update({k: name for k in layer})
    return values, sources


def effective_document(p, doc):
    values, _ = effective_format(p, doc)
    # Missing layout fields mean the original band layout. Keep the old digest
    # byte-for-byte until the user explicitly changes these settings.
    explicit=set(p['style']) | set(p.get('group_styles',{}).get(group_key(doc['folder']),{})) | set(overrides(doc))
    style={k:values[k] for k in (set(DEFAULT_STYLE) | (set(LEGACY_LAYOUT)&explicit))}
    return {**doc, **{k: values[k] for k in DEFAULT_LABELS}}, style


def revision(p, doc):
    effective, style = effective_document(p, doc)
    return review_digest(effective, style)


def document_ready(p, doc):
    # Choosing an existing, finished PDF does not claim a visual review.
    # Source integrity, paths and reference targets are validated separately.
    return doc["mode"] == "passthrough" or doc["approved"] == revision(p, doc)


def safe_path(folder, filename):
    joined = f"{folder}/{filename}" if folder else filename
    if "\\" in joined or joined.startswith("/") or len(joined) > 200:
        raise ValueError("Используйте относительный путь с /, длиной до 200 символов.")
    for part in joined.split("/"):
        if not part or part in (".", "..") or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((" ", ".")):
            raise ValueError("Недопустимое имя файла или папки.")
        if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", part.split(".")[0], re.I):
            raise ValueError("Имя зарезервировано Windows.")
    if not filename.lower().endswith(".pdf") or "/" in filename:
        raise ValueError("Выходное имя должно быть именем PDF без папок.")
    return str(PurePosixPath(joined))


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root else ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, pid):
        if not re.fullmatch(r"[a-f0-9]{32}", pid):
            raise ValueError("Некорректный проект.")
        return self.root / pid

    def load(self, pid):
        try:
            p = json.loads((self.folder(pid) / "project.json").read_text("utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("Проект не найден.") from exc
        if not isinstance(p, dict) or p.get('id') != pid:
            raise ValueError('Идентификатор проекта не соответствует папке. Файлы не изменены.')
        from .schema import upgrade
        return upgrade(self, p)

    def save(self, p):
        folder = self.folder(p["id"])
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / "project.tmp"
        with temp.open("w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, folder / "project.json")

    def list(self):
        return [{"id": f.parent.name, "name": json.loads(f.read_text("utf-8"))["name"]}
                for f in sorted(self.root.glob("*/project.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                if re.fullmatch(r'[a-f0-9]{32}', f.parent.name)]

    def create(self, name):
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("Введите название подачи (до 120 символов).")
        p = {"schema": 1, "id": uuid4().hex, "name": name.strip(), "documents": [], "main": None,
             "style": {**DEFAULT_STYLE,**NEW_LAYOUT}, "references": [], "footnotes": [], "links_reviewed": False}
        self.save(p)
        return p

    def source(self, p, source):
        if not source or not re.fullmatch(r"[a-f0-9]{64}\.(pdf|docx)", source.get("blob", "")):
            raise ValueError("Исходный файл отсутствует.")
        path = self.folder(p["id"]) / "inputs" / source["blob"]
        if not path.is_file():
            raise ValueError("Исходный файл отсутствует на диске.")
        data = path.read_bytes()
        if sha256(data).hexdigest() != source["sha256"]:
            raise ValueError("Исходный файл изменён вне инструмента. Замените его и проверьте результат заново.")
        return data

    def put(self, p, name, data, kind):
        if not data or len(data) > 50_000_000 or not name.lower().endswith("."+kind):
            raise ValueError(f"Ожидается {kind.upper()} размером до 50 МБ.")
        meta = pdf.inspect_pdf(data) if kind == "pdf" else {}
        if kind == "docx":
            word.package(data)
        hash_ = sha256(data).hexdigest()
        folder = self.folder(p["id"]) / "inputs"
        folder.mkdir(exist_ok=True)
        blob = f"{hash_}.{kind}"
        path = folder / blob
        if not path.exists():
            with path.open("xb") as f:
                f.write(data)
        return {"name": Path(name).name, "blob": blob, "sha256": hash_, **meta}

    def document(self, p, did):
        for doc in p["documents"]:
            if doc["id"] == did:
                return doc
        raise ValueError("Документ не найден.")

    def upload(self, p, name, data, kind="original", did=None, mode="prepare"):
        if mode not in ("prepare", "passthrough") or (mode == "passthrough" and (kind != "original" or did)):
            raise ValueError("Загрузка готового PDF доступна только для нового приложения.")
        source = self.put(p, name, data, "docx" if kind == "main" else "pdf")
        if kind == "main":
            previous = deepcopy(p)
            prior_bindings = p.get('restored_word_bindings', {}).get((p.get('main') or {}).get('sha256'), [])
            if prior_bindings:
                from .word_bridge import parse_link
                parts = word.package(data)
                present = [parse_link(r.get('Target', '')) for r in word.xml(parts[word.RELS])] if word.RELS in parts else []
                inherited = [marker for marker in prior_bindings if marker in present]
                if inherited: p['restored_word_bindings'][source['sha256']] = inherited
            if p.get('main', {}) and p['main']['sha256'] == source['sha256']:
                p['main'] = source
                self.save(p)
                return p
            p["main"] = source
            p["references"], p["footnotes"] = [], []
            p['excluded_references'] = []
            p["links_reviewed"] = False
            p["scanned"] = False
            if previous.get('main'):
                from .revisions import reconcile
                documents = [{**d, 'identifier': identifier(d, p)} for d in p['documents']]
                found = word.scan(data, documents, project_id=p['id'], bindings=p.get('restored_word_bindings', {}).get(source['sha256'], []))
                from .word_bridge import annotate
                annotate(found, p)
                p['edition_report'] = reconcile(previous, found)
                p.update(found)
                p['scanned'] = True
        elif did:
            doc = self.document(p, did)
            if kind not in ("original", "translation"):
                raise ValueError("Некорректная часть документа.")
            if kind == "translation" and doc["mode"] == "passthrough":
                raise ValueError("Сначала переключите документ в режим подготовки.")
            doc[kind] = source
            doc["selection" if kind == "original" else "translation_selection"] = [{"page": n+1} for n in range(source["pages"])]
            doc["translation_confirmed"] = False
            doc["approved"] = None
        else:
            stem = Path(name).stem
            doc = {"id": uuid4().hex, "title": stem, "prefix": "", "number": None, "designation": "Exhibit",
                   "filename": Path(name).name, "folder": "", "language": "", "mode": mode,
                   "original_label": "[Original]", "translation_label": "[Translation]", "original": source,
                   "translation": None, "translation_confirmed": False, "approved": None, "aliases": [],
                   "selection": [{"page": n+1} for n in range(source["pages"])], "translation_selection": [], "style": None,
                   "format_overrides": {}, "filename_mode": "source"}
            p["documents"].append(doc)
            p["links_reviewed"] = False
            p["scanned"] = False
        self.save(p)
        return p

    def use_originals(self, p, ids):
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
            raise ValueError("Выберите документы для использования без обработки.")
        proposed = deepcopy(p)
        selected = [self.document(proposed, did) for did in ids]
        translated = [d for d in selected if d["translation"]]
        if translated:
            raise ValueError(f"У документа «{translated[0]['title']}» прикреплён перевод. Открепите перевод или снимите выбор этого документа. Ничего не изменено.")
        for doc in selected:
            self.source(proposed, doc["original"])
            doc["mode"] = "passthrough"
            # A later return to preparation must require a fresh visual check.
            doc["approved"] = None
        self.save(proposed)
        return proposed

    def update(self, p, did, changes, persist=True):
        if not changes.keys() <= EDITABLE:
            raise ValueError("Неизвестные настройки документа.")
        doc = self.document(p, did)
        before_identifier = identifier(doc,p)
        before = {k: doc.get(k) for k in ("prefix", "number", "filename", "folder", "title", "aliases")}
        draft = {**doc, **changes}
        if not isinstance(draft.get('short_title', ''), str) or len(draft.get('short_title', '')) > 250:
            raise ValueError('Короткое название: не более 250 символов.')
        if draft["mode"] not in ("prepare", "passthrough") or draft["designation"] not in ("Exhibit", "Annex", ""):
            raise ValueError("Некорректный режим или обозначение.")
        if draft["number"] is not None and (type(draft["number"]) is not int or draft["number"] < 1):
            raise ValueError("Номер должен быть положительным целым числом.")
        if not isinstance(draft['prefix'],str) or (draft["prefix"] and not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*", draft["prefix"])):
            raise ValueError("Префикс: латинские буквы, цифры и дефисы, например AU-LA.")
        for key in ("title", "language", "original_label", "translation_label", "prefix", "folder", "filename"):
            if not isinstance(draft[key], str) or len(draft[key]) > 250:
                raise ValueError("Слишком длинное или некорректное поле.")
        if not draft["title"].strip() or not isinstance(draft["aliases"], list) or any(not isinstance(x, str) or len(x) > 250 for x in draft["aliases"]):
            raise ValueError("Введите название и корректные варианты упоминаний.")
        safe_path(draft["folder"], draft["filename"])
        pdf.validate_selection(draft["selection"], draft["original"]["pages"])
        if draft["translation"]:
            pdf.validate_selection(draft["translation_selection"], draft["translation"]["pages"])
        if draft["mode"] == "passthrough" and draft["translation"]:
            raise ValueError("Открепите перевод перед включением режима без обработки.")
        if draft.get("style") is not None:
            check_style(draft["style"])
        if draft.get("filename_mode", "manual") not in ("source", "manual", "generated"):
            raise ValueError("Некорректный режим имени файла.")
        if "format_overrides" in changes:
            check_format(changes["format_overrides"])
        layer = deepcopy(changes.get("format_overrides", overrides(doc)))
        layer.update({k: v for k, v in changes.items() if k in DEFAULT_LABELS})
        if "style" in changes:
            for key in set(DEFAULT_STYLE) | set(LEGACY_LAYOUT): layer.pop(key, None)
            layer.update(changes["style"] or {})
        if "mode" in changes and changes["mode"] != doc["mode"]:
            doc["approved"] = None
        doc.update(changes)
        doc["format_overrides"] = layer
        if "filename" in changes:
            doc["filename_mode"] = changes.get("filename_mode", "manual")
        if before != {k: doc.get(k) for k in before} or before_identifier != identifier(doc,p):
            p["links_reviewed"] = False
            p["scanned"] = False
        if persist: self.save(p)
        return p

    def style(self, p, style):
        check_style(style)
        unchanged = [d for d in p["documents"] if d["mode"] == "passthrough" and d["approved"] == revision(p, d)]
        p["style"] = style
        for d in unchanged: d["approved"] = revision(p, d)
        self.save(p)
        return p

    def prepare(self, p, doc):
        original = self.source(p, doc["original"])
        translation = self.source(p, doc["translation"]) if doc["translation"] else None
        effective, style = effective_document(p, doc)
        return pdf.assemble(original, translation, effective, style)

    def approve(self, p, did, action):
        doc = self.document(p, did)
        if action == "translation":
            if not doc["translation"]:
                raise ValueError("Перевод не прикреплён.")
            self.source(p, doc["translation"])
            doc["translation_confirmed"] = True
        elif action == "remove_translation":
            doc["translation"] = None
            doc["translation_selection"] = []
            doc["translation_confirmed"] = False
        elif action == "document":
            if doc["translation"] and not doc["translation_confirmed"]:
                raise ValueError("Сначала подтвердите проверку перевода.")
            self.prepare(p, doc)
            doc["approved"] = revision(p, doc)
        else:
            raise ValueError("Неизвестное действие проверки.")
        self.save(p)
        return p

    def scan(self, p):
        if not p["main"]:
            raise ValueError("Сначала загрузите основной DOCX.")
        saved = {r["key"]: r for r in p["references"] if r.get("manual") or r.get('scope_manual')}
        documents = [{**d, "identifier": identifier(d,p)} for d in p["documents"]]
        found = word.scan(self.source(p, p["main"]), documents, saved, excluded=p.get('excluded_references', []), project_id=p['id'], bindings=p.get('restored_word_bindings', {}).get(p['main']['sha256'], []))
        from .word_bridge import annotate
        annotate(found, p)
        # Preserve explicit user-added free-text spans while the source is unchanged.
        keys = {r["key"] for r in found["references"]}
        for r in p["references"]:
            if r.get("custom") and r["key"] not in keys:
                found["references"].append(r)
        p.update(found)
        p["scanned"], p["links_reviewed"] = True, False
        self.save(p)
        return p

    def map_reference(self, p, key, target, all_same=False, keep_original=False):
        if type(keep_original) is not bool or (keep_original and target is not None):
            raise ValueError("Выберите файл либо вариант «Без файла».")
        if target is not None:
            self.document(p, target)
        ref = next((r for r in p["references"] if r["key"] == key), None)
        if not ref:
            raise ValueError("Упоминание не найдено.")
        for r in p["references"]:
            if r["key"] == key or (all_same and r["mention"] == ref["mention"]):
                r.update(target=target, manual=True, keep_original=keep_original)
        p["links_reviewed"] = False
        self.save(p)
        return p

    def exclude_reference(self, p, key, restore=False):
        proposed = deepcopy(p)
        source = proposed.setdefault('excluded_references', []) if restore else proposed['references']
        ref = next((r for r in source if r['key']==key), None)
        if ref is None: raise ValueError('Упоминание не найдено. Обновите список.')
        source.remove(ref)
        if restore:
            para = next(f for f in p['footnotes'] if f['fid']==ref['fid'] and f['paragraph']==ref['paragraph'])
            peers = [r for r in proposed['references'] if r['fid']==ref['fid'] and r['paragraph']==ref['paragraph']]
            automatic = deepcopy([*peers,ref])
            citations.propose(para['text'], automatic)
            by_key = {r['key']:r for r in automatic}
            for item in [*peers,ref]:
                if not item.get('scope_manual'):
                    item.update({k:by_key[item['key']][k] for k in ('link_start','link_end','link_text','scope_review')})
            citations.validate(para['text'], [*peers,ref])
            # Restoring even a now-unrecognized identifier is an explicit choice.
            ref.update(custom=True, manual=True)
            proposed['references'].append(ref)
            proposed['links_reviewed'] = False
            self.save(proposed)
        else:
            proposed.setdefault('excluded_references', []).append(ref)
            self.scan(proposed)
        p.update(proposed)
        return p

    def add_reference(self, p, fid, pi, mention, start=None, end=None, target=None):
        para = next((x for x in p["footnotes"] if x["fid"] == fid and x["paragraph"] == pi), None)
        if not para or not mention.strip():
            raise ValueError("Выберите сноску и точный текст упоминания.")
        if target is not None: self.document(p, target)
        if start is not None or end is not None:
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(para['text']) or para['text'][start:end] != mention:
                raise ValueError('Выделите точный текст ссылки в сноске.')
            spans = [(start,end)]
        else:
            spans = [m.span() for m in re.finditer(re.escape(mention), para['text'])]
        matches = spans
        if not matches:
            raise ValueError("Текст не найден в этой сноске.")
        added = 0
        for a,b in matches:
            if any(r["fid"] == fid and r["paragraph"] == pi and citations.bounds(r)[0] < b and citations.bounds(r)[1] > a for r in p["references"]):
                continue
            p["references"].append({"key": f"{fid}:{pi}:{a}:{b}", "fid": fid,
                "footnote": para["footnote"], "paragraph": pi, "start": a, "end": b,
                "mention": mention, "target": target, "candidates": [], "manual": True, "custom": True,
                "link_start":a, "link_end":b, "link_text":mention, "scope_manual":True})
            p['excluded_references'] = [r for r in p.get('excluded_references', []) if not (r['fid']==fid and r['paragraph']==pi and a < r['end'] and b > r['start'])]
            added += 1
        if not added:
            raise ValueError("Упоминание уже сопоставляется или пересекается с другим.")
        p["links_reviewed"] = False
        self.save(p)
        return p

    def reference_range(self,p,key,start,end):
        ref=next((r for r in p['references'] if r['key']==key),None)
        if ref is None:raise ValueError('Ссылка не найдена. Повторите сопоставление.')
        para=next(f for f in p['footnotes'] if f['fid']==ref['fid'] and f['paragraph']==ref['paragraph'])
        if type(start) is not int or type(end) is not int:raise ValueError('Выделите текст ссылки в сноске.')
        changed={**ref,'link_start':start,'link_end':end,'link_text':para['text'][start:end],'scope_manual':True,'scope_review':False}
        peers=[changed if r['key']==key else r for r in p['references'] if r['fid']==ref['fid'] and r['paragraph']==ref['paragraph']]
        citations.validate(para['text'],peers)
        ref.update(changed);p['links_reviewed']=False;self.save(p)
        return p

    def confirm_links(self, p):
        if not p.get("scanned"):
            raise ValueError("Запустите сопоставление заново.")
        if any(not r["target"] and not r.get('keep_original') for r in p["references"]):
            raise ValueError("Остались несопоставленные упоминания.")
        word.add_links(self.source(p, p["main"]), p["references"], {d["id"]: safe_path(d["folder"], d["filename"]) for d in p["documents"]})
        p["links_reviewed"] = True
        self.save(p)
        return p

    def validate(self, p):
        issues, ids, paths = [], {}, ({"main document.docx": None, "main document.pdf": None} if p["main"] else {})
        def issue(code, message, doc=None, ref=None):
            issues.append({"code": code, "message": message, "document": doc, "reference": ref, "blocking": True})
        if not p["documents"] and not p["main"]:
            issue("empty", "Добавьте PDF или основной DOCX.")
        for d in p["documents"]:
            ident = identifier(d,p)
            if d["mode"] == "prepare" and effective_format(p, d)[0]["designation"] and d["number"] is None:
                issue("number", "Задайте номер приложения или выберите обозначение «Без слова».", d["id"])
            if ident and ident.casefold() in ids:
                issue("duplicate_id", f"Повторяется полный идентификатор {ident}.", d["id"])
            ids[ident.casefold()] = d["id"]
            try:
                path = safe_path(d["folder"], d["filename"])
                canonical = unicodedata.normalize("NFC", path).casefold()
                if canonical in paths:
                    issue("duplicate_path", f"Повторяется выходной путь {path}.", d["id"])
                paths[canonical] = d["id"]
                self.source(p, d["original"])
                if d["translation"]:
                    self.source(p, d["translation"])
                    if not d["translation_confirmed"]:
                        issue("translation", "Перевод ожидает проверки.", d["id"])
                if not document_ready(p, d):
                    issue("unreviewed", "Просмотрите результат и подтвердите подготовку.", d["id"])
            except ValueError as exc:
                issue("source", str(exc), d["id"])
        # A file cannot also be an ancestor directory of another file.
        for path, did in paths.items():
            if any(parent.as_posix().casefold() in paths for parent in PurePosixPath(path).parents if str(parent) != "."):
                issue("path_hierarchy", "Файл конфликтует с названием папки другого документа.", did)
        if p["main"]:
            try:
                self.source(p, p["main"])
            except ValueError as exc:
                issue("main", str(exc))
            if not p.get("scanned") or not p["links_reviewed"]:
                issue("links_review", "Перепроверьте сноски и подтвердите сопоставление.")
            doc_ids = {d["id"] for d in p["documents"]}
            for r in p["references"]:
                if not r.get('keep_original') and r["target"] not in doc_ids:
                    issue("unmatched", f"Сноска {r['footnote']}: не сопоставлено «{r['mention']}».", ref=r["key"])
        return issues

    def linked_main(self, p):
        if not p["main"]: raise ValueError("Сначала загрузите основной DOCX.")
        paths = {d["id"]: safe_path(d["folder"], d["filename"]) for d in p["documents"]}
        data = word.add_links(self.source(p, p["main"]), p["references"], paths)
        return data, paths

    def main_pdf_key(self, p):
        data, paths = self.linked_main(p)
        # ZIP entry timestamps are irrelevant to the rendered document.
        key = digest({"parts": {k: sha256(v).hexdigest() for k, v in word.package(data).items()}, "paths": paths, "version": main_pdf.CONVERSION_VERSION})
        return key, data, paths

    def main_pdf_status(self, p):
        engine = main_pdf.available()
        result = {k: v for k, v in engine.items() if k != "executable"}
        result["ready"] = False
        if p["main"] and p.get("scanned") and p["links_reviewed"]:
            key, _, _ = self.main_pdf_key(p)
            folder = self.folder(p["id"]) / "main-pdf"
            try:
                meta = json.loads((folder / (key+".json")).read_text("utf-8"))
                if sha256((folder / (key+".pdf")).read_bytes()).hexdigest() == meta["sha256"]:
                    result.update(ready=True, pages=meta["pages"], converted_with=meta["engine"], key=key)
            except (OSError, ValueError, KeyError): pass
        return result

    def prepare_main_pdf(self, p, refresh=False):
        issues = self.validate(p)
        if issues: raise ValueError("Подготовка PDF заблокирована: " + issues[0]["message"])
        key, data, paths = self.main_pdf_key(p)
        folder = self.folder(p["id"]) / "main-pdf"
        if not refresh and self.main_pdf_status(p)["ready"]:
            return (folder / (key+".pdf")).read_bytes()
        result = main_pdf.convert_docx(data, list(paths.values()))
        folder.mkdir(exist_ok=True)
        meta = {"sha256": sha256(result).hexdigest(), "pages": pdf.inspect_pdf(result)["pages"], "engine": main_pdf.available()["label"]}
        with LOCK:
            temp = folder / (key+".tmp")
            temp.write_bytes(result); os.replace(temp, folder / (key+".pdf"))
            temp.write_text(json.dumps(meta), encoding="utf-8"); os.replace(temp, folder / (key+".json"))
        return result

    def read_main_pdf(self, p):
        status = self.main_pdf_status(p)
        if not status["ready"]: raise ValueError("Основной PDF ещё не подготовлен или устарел. Подготовьте его заново.")
        return (self.folder(p["id"]) / "main-pdf" / (status["key"]+".pdf")).read_bytes()

    def export(self, p):
        issues = self.validate(p)
        if issues:
            raise ValueError("Сборка заблокирована: " + issues[0]["message"])
        out, paths = BytesIO(), {}
        # Build in memory: no partial or stale ready package is ever served.
        with ZipFile(out, "w", ZIP_DEFLATED) as z:
            for d in p["documents"]:
                path = safe_path(d["folder"], d["filename"])
                paths[d["id"]] = path
                z.writestr("Submission/"+path, self.prepare(p, d))
            if p["main"]:
                z.writestr("Submission/Main document.docx", self.linked_main(p)[0])
                z.writestr("Submission/Main document.pdf", self.prepare_main_pdf(p))
        data = out.getvalue()
        from .history import save_export
        save_export(self, p, data)
        return data

    def public(self, p):
        result = deepcopy(p)
        for d in result["documents"]:
            d["identifier"] = identifier(d,p)
            d["ready"] = document_ready(p, d)
            d["effective_format"], d["format_sources"] = effective_format(p, d)
            d["format_overrides"] = overrides(d)
            d["filename_mode"] = d.get("filename_mode", "manual")
        result["format_defaults"] = {**LEGACY_LAYOUT, **DEFAULT_LABELS, **p.get("format", {}), **p["style"]}
        result["group_formats"] = {d["folder"]: {**result["format_defaults"], **p.get("group_styles", {}).get(group_key(d["folder"]), {})} for d in p["documents"]}
        result["issues"] = self.validate(p)
        return result

    def batch_plan(self, p, request):
        from .batch import plan
        return plan(self, p, request)

    def batch_apply(self, p, request, token):
        proposed, report = self.batch_plan(p, request)
        if token != report["token"]:
            raise ValueError("Проект или настройки изменились. Обновите предварительную проверку.")
        if report["conflicts"]:
            raise ValueError("Изменения не применены: " + report["conflicts"][0]["message"])
        self.save(proposed)
        return proposed


def check_style(style):
    if not isinstance(style, dict) or not set(DEFAULT_STYLE) <= set(style) or not set(style) <= set(DEFAULT_STYLE)|set(LEGACY_LAYOUT):
        raise ValueError("Ожидаются шрифт, размер и отступ штампа.")
    if style["font"] not in ("DejaVu", "Helvetica", "Times-Roman") or not 6 <= float(style["size"]) <= 24 or not 8 <= float(style["margin"]) <= 100:
        raise ValueError("Некорректные настройки штампа.")
    if style.get('stamp_mode','band') not in ('band','overlay') or not 8 <= float(style.get('top',26)) <= 150:
        raise ValueError('Режим штампа: в полях страницы или отдельная полоса; отступ сверху — 8–150 pt.')


def check_format(values):
    if not isinstance(values, dict) or not values.keys() <= FORMAT_KEYS:
        raise ValueError("Некорректные параметры оформления.")
    try:
        check_style({**DEFAULT_STYLE, **{k: v for k, v in values.items() if k in set(DEFAULT_STYLE)|set(LEGACY_LAYOUT)}})
    except (TypeError, ValueError):
        raise ValueError("Некорректные параметры шрифта или отступа.") from None
    if values.get("designation", "Exhibit") not in ("Exhibit", "Annex", ""):
        raise ValueError("Некорректное обозначение приложения.")
    for key in ("original_label", "translation_label"):
        if key in values and (not isinstance(values[key], str) or len(values[key]) > 250 or "\n" in values[key] or "\r" in values[key]):
            raise ValueError("Пометка должна занимать одну строку до 250 символов.")
