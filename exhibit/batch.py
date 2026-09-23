"""Side-effect-free batch previews followed by one atomic project write."""
from copy import deepcopy
from io import BytesIO
import re
from reportlab.pdfgen import canvas

from . import pdf
from .identifiers import stamp_label
from .project import (DEFAULT_STYLE, DEFAULT_LABELS, NEW_LAYOUT, LEGACY_LAYOUT, check_format, digest, effective_format,
                      group_key, identifier, overrides, revision, safe_path, document_ready)


def snapshot(p, d):
    values, sources = effective_format(p, d)
    return {"identifier": identifier(d,p), "folder": d["folder"], "filename": d["filename"],
            "path": safe_path(d["folder"], d["filename"]), "format": values, "sources": sources,
            "filename_mode": d.get("filename_mode", "manual")}


def plan(store, original, request):
    if not isinstance(request, dict) or request.get("action") not in ("organize", "format", "folders"):
        raise ValueError("Выберите массовую операцию.")
    p = deepcopy(original)
    ids = request.get("document_ids", [])
    if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("Некорректный список выбранных документов.")
    selected = [store.document(p, did) for did in ids]
    warnings = []
    if request['action'] == 'folders':
        destinations = request.get('destinations')
        if not isinstance(destinations, dict) or not destinations:
            raise ValueError('Выберите документы и папки назначения.')
        for did, folder in destinations.items():
            if not isinstance(folder,str): raise ValueError('Введите название папки.')
            store.update(p, did, {'folder':folder}, persist=False)
        # Paths do not change which exhibit a mention refers to. Conversion cache
        # keys include paths, so DOCX/PDF links will be rebuilt at export time.
        if all(identifier(d,original)==identifier(store.document(p,d['id']),p) for d in original['documents']):
            p['scanned'] = original.get('scanned',False)
            p['links_reviewed'] = original.get('links_reviewed',False)
        warnings.append('Главный DOCX и PDF останутся в корне Submission. Ссылки в новом комплекте будут построены с учётом папок. После сборки сохраняйте структуру папок.')
    elif request["action"] == "organize":
        if not selected:
            raise ValueError("Выберите документы для систематизации.")
        folder = request.get("folder")
        if folder is not None:
            if not isinstance(folder, str): raise ValueError("Введите папку.")
            for d in selected: store.update(p, d["id"], {"folder": folder}, persist=False)
        numbering = request.get("numbering")
        if numbering is not None:
            if not isinstance(numbering, dict): raise ValueError("Некорректная нумерация.")
            prefix, n = numbering.get("prefix"), numbering.get("start")
            if not isinstance(prefix, str) or (prefix and not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*", prefix)):
                raise ValueError("Укажите префикс, например RLA или AU-LA.")
            if type(n) is not int or n < 1: raise ValueError("Начальный номер должен быть положительным целым числом.")
            occupied = {identifier(d,p).casefold() for d in p["documents"] if d["number"] is not None}
            for d in selected:
                if d["number"] is not None: continue
                while identifier({**d,'prefix':prefix,'number':n},p).casefold() in occupied: n += 1
                store.update(p, d["id"], {"prefix": prefix, "number": n}, persist=False)
                occupied.add(identifier(d,p).casefold())
                n += 1
            warnings.append("Уже назначенные номера и префиксы сохранены; занятые номера пропущены. Новые номера идут в порядке строк предпросмотра.")
        names = request.get("names", "keep")
        if names not in ("keep", "identifier", "identifier_title"):
            raise ValueError("Неизвестное правило имён файлов.")
        if names != "keep":
            kept = []
            for d in selected:
                if d.get("filename_mode", "manual") == "manual":
                    kept.append(d["title"])
                    continue
                if not identifier(d,p): continue
                title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", d["title"]).strip(" .")[:90].rstrip(" .")
                name = identifier(d,p) + (" — " + title if names == "identifier_title" and title else "") + ".pdf"
                store.update(p, d["id"], {"filename": name}, persist=False)
                d["filename_mode"] = "generated"
            if kept: warnings.append(f"Сохранены имена, заданные вручную или в прежней версии: {len(kept)}.")
    else:
        values = request.get("values", {})
        check_format(values)
        scope, reset = request.get("scope"), request.get("reset", False)
        if type(reset) is not bool or type(request.get("reset_overrides", False)) is not bool:
            raise ValueError("Некорректный сброс настроек.")
        if reset and values: raise ValueError("Для сброса не передавайте новые значения.")
        if scope == "project":
            affected = p["documents"]
            if reset:
                p["style"], p["format"] = {**DEFAULT_STYLE,**NEW_LAYOUT}, deepcopy(DEFAULT_LABELS)
            else:
                p["style"].update({k: v for k, v in values.items() if k in set(DEFAULT_STYLE)|set(LEGACY_LAYOUT)})
                p.setdefault("format", {}).update({k: v for k, v in values.items() if k in DEFAULT_LABELS})
        elif scope == "group":
            folder = request.get("group")
            if not isinstance(folder, str): raise ValueError("Выберите папку группы.")
            safe_path(folder, "check.pdf")
            key = group_key(folder)
            affected = [d for d in p["documents"] if group_key(d["folder"]) == key]
            if not affected: raise ValueError("В выбранной группе нет документов.")
            layer = p.setdefault("group_styles", {}).setdefault(key, {})
            if reset: layer.clear()
            else: layer.update(values)
        elif scope == "selection":
            if not selected: raise ValueError("Выберите документы для оформления.")
            affected = selected
            for d in affected:
                layer = {} if reset else overrides(d)
                layer.update(values)
                store.update(p, d["id"], {"format_overrides": layer}, persist=False)
        else:
            raise ValueError("Выберите уровень оформления.")
        if scope != "selection" and request.get("reset_overrides"):
            for d in affected: store.update(p, d["id"], {"format_overrides": {}}, persist=False)
        warnings.append("Индивидуальные настройки имеют приоритет над группой, группа — над подачей. Готовые PDF в режиме без обработки не штампуются.")
    # A folder move may change the inherited format even if no label was edited directly.
    changes = []
    original_docs = {d["id"]: d for d in original["documents"]}
    order = ids + [d["id"] for d in p["documents"] if d["id"] not in ids]
    by_id = {d["id"]: d for d in p["documents"]}
    for did in order:
        d, old = by_id[did], original_docs[did]
        before, after = snapshot(original, old), snapshot(p, d)
        if (request['action']=='folders' and old['approved']==revision(original,old)
                and before['format']==after['format']):
            d['approved'] = revision(p,d)
        if before['identifier'] != after['identifier']:
            p['links_reviewed']=False
            p['scanned']=False
        # Formatting has no effect on a byte-preserved PDF. Keep its existing review
        # only if its identifier and destination are also unchanged.
        if (d["mode"] == "passthrough" and old["approved"] == revision(original, old)
                and before["identifier"] == after["identifier"] and before["path"] == after["path"]):
            d["approved"] = revision(p, d)
        if before != after:
            changes.append({"id": did, "title": d["title"], "before": before, "after": after,
                            "requires_review": not document_ready(p, d)})
    conflicts = [x for x in store.validate(p) if x["code"] in ("duplicate_id", "duplicate_path", "path_hierarchy", "source", "main")]
    for change in changes:
        d = by_id[change["id"]]
        if d["mode"] == "passthrough" or (request['action']=='folders' and change['before']['format']==change['after']['format']): continue
        values, _ = effective_format(p, d)
        right = stamp_label({**d,'designation':values['designation']})
        try:
            pdf.init_font()
            for source, selection, label in [(d["original"], d["selection"], "original_label"), (d["translation"], d["translation_selection"], "translation_label")]:
                if source:
                    width = min(source["sizes"][item["page"]-1][0] for item in selection)
                    pdf.stamp(canvas.Canvas(BytesIO()), width, 1000, values[label], right, values)
                    if values['stamp_mode']=='overlay':pdf.prepare_part(store.source(p,source),selection,values[label],right,values)
        except ValueError as exc:
            conflicts.append({"code": "stamp", "message": f"{d['title']}: {exc}", "document": d["id"]})
    return p, {"token": digest({"project": original, "request": request}), "changes": changes,
               "conflicts": conflicts, "warnings": warnings, "links_need_review": bool(original.get("links_reviewed") and not p.get("links_reviewed")),
               "settings_changed": p != original}
