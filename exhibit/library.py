"""Local immutable material versions; projects always own independent copies."""
from copy import deepcopy
from datetime import datetime, timezone, date
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import json
import os
import re
import shutil
import tempfile
import unicodedata
import time

from . import pdf
from .backup import json_data, hash_stream, io_errors
from .project import LOCK, digest, effective_format, identifier, safe_path, document_ready, check_format

SCHEMA = 1
ID = re.compile(r'[a-f0-9]{32}')
HASH = re.compile(r'[a-f0-9]{64}')
ROLES = {'original', 'translation', 'ready'}
FIELDS = ('title', 'short_title', 'language', 'category', 'document_date', 'tags')
COPY_FIELDS = ('title', 'short_title', 'language', 'selection', 'translation_selection', 'format_overrides', 'original', 'translation', 'translation_confirmed')


def uid(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Некорректный идентификатор библиотеки, материала или операции.')
    return value


def clean_text(value, limit=250):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('Некорректный или слишком длинный реквизит материала.')
    return value.strip()


def metadata(value):
    out = {key: clean_text(value.get(key, '')) for key in FIELDS if key != 'tags'}
    if not out['title']: raise ValueError('Введите название материала.')
    if out['document_date']:
        try: date.fromisoformat(out['document_date'])
        except ValueError as exc: raise ValueError('Дата документа: ГГГГ-ММ-ДД.') from exc
    tags = value.get('tags', [])
    if not isinstance(tags, list) or len(tags) > 50: raise ValueError('Допустимо до 50 меток.')
    out['tags'] = sorted(set(clean_text(tag, 80) for tag in tags if clean_text(tag, 80)))
    return out


def binding(doc):
    return {'original': doc['original']['sha256'], 'selection': digest(doc['selection']),
            'translation': doc['translation']['sha256'] if doc.get('translation') else None,
            'translation_selection': digest(doc['translation_selection'])}


def translation_valid(doc):
    return bool(doc.get('translation') and doc.get('translation_confirmed')
                and (not doc.get('translation_binding') or doc['translation_binding'] == binding(doc)))


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.'+path.name+'-'+uuid4().hex+'.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(',', ':'))
            stream.flush(); os.fsync(stream.fileno())
        # Windows may briefly retain a reader's file handle across a status poll.
        for attempt in range(8):
            try: os.replace(temp,path); break
            except PermissionError:
                if os.name!='nt' or attempt==7: raise
                time.sleep(.015*(attempt+1))
    finally: temp.unlink(missing_ok=True)


def check_version(v):
    if not isinstance(v, dict) or type(v.get('schema')) is not int or v.get('schema') != SCHEMA: raise ValueError('Версия формата библиотеки не поддерживается.')
    for key in ('library_id', 'material_id', 'id'): uid(v.get(key))
    if v.get('parent') is not None: uid(v['parent'])
    metadata(v.get('metadata', {}))
    try: datetime.fromisoformat(v['created_at'])
    except (KeyError, TypeError, ValueError) as exc: raise ValueError('Некорректная дата версии.') from exc
    clean_text(v.get('comment', ''), 1000)
    parts = v.get('parts')
    if not isinstance(parts, dict) or not parts or not set(parts) <= ROLES or ('original' not in parts and 'ready' not in parts):
        raise ValueError('Версия должна содержать оригинал или готовый PDF.')
    for part in parts.values():
        if (not isinstance(part, dict) or not HASH.fullmatch(str(part.get('sha256', '')))
                or part.get('blob') != part['sha256']+'.pdf' or type(part.get('size')) is not int
                or not 0 < part['size'] <= 50_000_000 or type(part.get('pages')) is not int
                or not 0 < part['pages'] <= 1000 or len(part.get('sizes', [])) != part['pages']):
            raise ValueError('Повреждены сведения о части материала.')
        clean_text(part.get('name', ''))
    recipe = v.get('recipe', {})
    check_format(recipe.get('format', {}))
    if 'original' in parts: pdf.validate_selection(recipe.get('selection'), parts['original']['pages'])
    if 'translation' in parts:
        if 'original' not in parts: raise ValueError('Перевод должен быть привязан к оригиналу.')
        pdf.validate_selection(recipe.get('translation_selection'), parts['translation']['pages'])
        expected = {'original': parts['original']['sha256'], 'selection': digest(recipe['selection']),
                    'translation': parts['translation']['sha256'], 'translation_selection': digest(recipe['translation_selection'])}
        if v.get('translation_binding') != expected: raise ValueError('Подтверждение перевода не соответствует оригиналу/выборке.')
    return v


class Library:
    def __init__(self, store):
        self.store = store
        self.root = store.root/'libraries'
        self.work = store.root/'.library-work'
        self.cache = {}

    def folder(self, lid):
        self.no_links(self.root)
        folder = self.root/uid(lid)
        self.no_links(folder)
        return folder

    @staticmethod
    def no_links(path):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Файлы библиотеки не должны быть ссылками файловой системы.')

    def load(self, lid):
        path = self.folder(lid)/'library.json'
        self.no_links(path)
        try:
            stamp = (path.stat().st_mtime_ns, path.stat().st_size)
            if lid in self.cache and self.cache[lid][0] == stamp: return deepcopy(self.cache[lid][1])
            lib = json_data(path.read_bytes())
            if type(lib.get('schema')) is not int or lib.get('schema') != SCHEMA or lib.get('id') != lid or type(lib.get('revision')) is not int:
                raise ValueError('Формат или идентичность библиотеки не поддерживается.')
            if not clean_text(lib.get('name'), 120) or not isinstance(lib.get('materials'), dict) or not isinstance(lib.get('operations'), dict):
                raise ValueError('Повреждён каталог библиотеки.')
            for mid, item in lib['materials'].items():
                uid(mid)
                if item.get('id') != mid or type(item.get('hidden')) is not bool or not item.get('versions'):
                    raise ValueError('Повреждена карточка материала.')
                if len(set(item['versions'])) != len(item['versions']): raise ValueError('Повторяющаяся версия материала.')
                for vid in item['versions']: uid(vid)
                metadata(item['latest']['metadata'])
            self.cache[lid] = (stamp, deepcopy(lib))
            return lib
        except FileNotFoundError as exc: raise ValueError('Библиотека недоступна. Файлы подачи остаются доступны.') from exc
        except (KeyError, TypeError, AttributeError) as exc: raise ValueError('Каталог библиотеки повреждён; это не пустой результат поиска.') from exc

    def create(self, name):
        name = clean_text(name, 120)
        if not name: raise ValueError('Введите название библиотеки.')
        lib = {'schema': SCHEMA, 'id': uuid4().hex, 'name': name, 'revision': 0, 'materials': {}, 'operations': {}}
        with LOCK, io_errors(): atomic_json(self.folder(lib['id'])/'library.json', lib)
        return lib

    def list(self):
        if not self.root.exists(): return []
        rows=[]
        for folder in sorted(self.root.iterdir()):
            if not ID.fullmatch(folder.name) or not folder.is_dir(): continue
            try:
                lib=self.load(folder.name);rows.append({k:lib[k] for k in ('id','name','revision')})
            except (ValueError,OSError) as exc:
                rows.append({'id':folder.name,'name':'Недоступная библиотека','revision':None,'error':str(exc)})
        return rows

    def catalog(self, lid, query='', offset=0, limit=50, sort='title', **filters):
        if not 0 <= offset or not 1 <= limit <= 100: raise ValueError('Некорректная страница каталога.')
        lib = self.load(lid); query = unicodedata.normalize('NFC', query).casefold()
        rows = []
        for item in lib['materials'].values():
            head = item['latest']; meta = head['metadata']
            if item['hidden'] != (filters.get('hidden') == 'true'): continue
            haystack = ' '.join([meta['title'], meta['short_title'], *head.get('names', [])])
            if query not in unicodedata.normalize('NFC', haystack).casefold(): continue
            if any(filters.get(k) and filters[k] != meta[k] for k in ('category', 'language', 'document_date')): continue
            if filters.get('tag') and filters['tag'] not in meta['tags']: continue
            if filters.get('translation') in ('true', 'false') and head.get('translated', False) != (filters['translation'] == 'true'): continue
            rows.append({**deepcopy(item), 'version_count': len(item['versions'])})
        rows.sort(key=lambda x: x['latest']['created_at'] if sort == 'date' else x['latest']['metadata']['title'].casefold(), reverse=sort == 'date')
        return {'library': {k:lib[k] for k in ('id','name','revision')}, 'total':len(rows), 'items':rows[offset:offset+limit]}

    def version(self, lid, mid, vid=None, verify=False):
        lib = self.load(lid); item = lib['materials'].get(uid(mid))
        if not item: raise ValueError('Материал не найден.')
        vid = uid(vid or item['versions'][-1])
        if vid not in item['versions']: raise ValueError('Версия не принадлежит выбранному материалу.')
        path = self.folder(lid)/'versions'/f'{vid}.json'; self.no_links(path.parent); self.no_links(path)
        try: v = check_version(json_data(path.read_bytes()))
        except FileNotFoundError as exc: raise ValueError('Манифест версии отсутствует.') from exc
        if (v['library_id'], v['material_id'], v['id']) != (lid,mid,vid): raise ValueError('Идентичность версии не совпадает.')
        if verify:
            for part in v['parts'].values(): self.part(lid, part)
        return v

    def part(self, lid, part):
        path = self.folder(lid)/'blobs'/part['blob']; self.no_links(path.parent); self.no_links(path)
        try:
            with path.open('rb') as stream: actual = hash_stream(stream)
        except FileNotFoundError as exc: raise ValueError('Часть материала отсутствует на диске.') from exc
        if actual != {k:part[k] for k in ('sha256','size')}: raise ValueError('Часть материала повреждена: '+part['name'])
        return path

    def preview_page(self, lid, mid, vid, role, page, scale=1.2):
        v = self.version(lid,mid,vid); part = v['parts'].get(role)
        if not part: raise ValueError('В версии нет выбранной части.')
        return pdf.render_png(self.part(lid,part).read_bytes(),page,scale)

    def pending(self, token=None):
        self.no_links(self.work)
        folder = self.work/uid(token or uuid4().hex)
        self.no_links(folder)
        if token and not folder.is_dir(): raise ValueError('Предварительный выбор больше не доступен. Повторите его.')
        folder.mkdir(parents=True,exist_ok=True)
        return folder

    def stage_part(self, folder, name, data):
        if not data or len(data) > 50_000_000: raise ValueError('PDF должен быть не больше 50 МБ.')
        info = pdf.inspect_pdf(data); h = sha256(data).hexdigest(); blob = h+'.pdf'
        file = folder/blob
        if not file.exists():
            with file.open('xb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        return {'name':clean_text(Path(name).name), 'sha256':h,'blob':blob,'size':len(data),**info}

    def save_preview(self, lid, pid, rows, cancel=lambda:False, progress=lambda *args:None):
        self.check_rows(rows)
        with LOCK, io_errors():
            lib = self.load(lid); p = self.store.load(pid); folder = self.pending()
            planned = []; errors = []; warnings = []
            for row in rows:
                if cancel(): raise ValueError('Операция отменена до публикации.')
                progress(len(planned)+len(errors),len(rows),'Проверяем состав материалов')
                try:
                    doc = self.store.document(p, row['document_id']); parts = {}
                    if any(r['version']['material_id']==row.get('material_id') for r in planned): raise ValueError('Одна новая версия материала за одну операцию.')
                    role = 'ready' if doc['mode']=='passthrough' else 'original'
                    parts[role] = self.stage_part(folder, doc['original']['name'],self.store.source(p,doc['original']))
                    valid = translation_valid(doc)
                    if doc.get('translation') and not valid:
                        warnings.append({'document_id':doc['id'],'message':'Черновик перевода остаётся в подаче; можно сохранить только оригинал.'})
                        if not row.get('original_only'): raise ValueError('Явно выберите сохранение только оригинала: перевод не подтверждён.')
                    if valid and not row.get('original_only'):
                        parts['translation'] = self.stage_part(folder, doc['translation']['name'],self.store.source(p,doc['translation']))
                    if doc['mode']=='prepare' and document_ready(p,doc) and (not doc.get('translation') or 'translation' in parts):
                        parts['ready'] = self.stage_part(folder,doc['filename'],self.store.prepare(p,doc))
                    inherited=self.version(lid,row['material_id'])['metadata'] if row.get('material_id') else {}
                    meta = metadata({**inherited,**doc, **row.get('metadata',{})})
                    recipe = {'selection':deepcopy(doc['selection']) if role=='original' else [], 'translation_selection':deepcopy(doc['translation_selection']) if 'translation' in parts else [],
                              'format':effective_format(p,doc)[0]}
                    mid = row.get('material_id') or uuid4().hex; uid(mid)
                    if row.get('material_id') and mid not in lib['materials']: raise ValueError('Материал для новой версии не найден.')
                    parent = lib['materials'][mid]['versions'][-1] if mid in lib['materials'] else None
                    v = {'schema':SCHEMA,'id':uuid4().hex,'library_id':lid,'material_id':mid,'parent':parent,
                         'created_at':datetime.now(timezone.utc).isoformat(),'comment':clean_text(row.get('comment',''),1000),
                         'metadata':meta,'parts':parts,'recipe':recipe,'translation_binding':binding(doc) if 'translation' in parts else None,
                         'ready_identifier':identifier(doc,p) if 'ready' in parts else None}
                    check_version(v)
                    signature = digest({k:x['sha256'] for k,x in parts.items() if k != 'ready' or 'original' not in parts})
                    duplicates = [m['id'] for m in lib['materials'].values() if m['latest']['signature']==signature]
                    duplicates += [r['version']['material_id'] for r in planned if digest({k:x['sha256'] for k,x in r['version']['parts'].items() if k!='ready' or 'original' not in r['version']['parts']})==signature]
                    planned.append({'version':v,'duplicates':duplicates,'allow_duplicate':row.get('allow_duplicate',False)})
                except (ValueError,KeyError) as exc: errors.append({'document_id':row.get('document_id'),'message':str(exc)})
            plan = {'action':'save','library_id':lid,'revision':lib['revision'],'project_id':pid,'project_digest':digest(p),
                    'rows':planned,'errors':errors,'warnings':warnings}
            atomic_json(folder/'plan.json',plan)
            return {'token':folder.name,**plan}

    def publish(self, token, cancel=lambda:False, progress=lambda *args:None):
        folder = self.pending(token); plan = json_data((folder/'plan.json').read_bytes())
        if plan.get('action')!='save': raise ValueError('Повторите проверку состава для сохранения материала.')
        with LOCK, io_errors():
            lib = self.load(plan['library_id'])
            if token in lib['operations']: return lib['operations'][token]
            if plan['errors'] or not plan['rows']: raise ValueError('Исключите ошибочные строки и повторите предварительную проверку.')
            if lib['revision'] != plan['revision']: raise ValueError('Библиотека изменилась. Повторите предварительную проверку.')
            if plan.get('project_id') and digest(self.store.load(plan['project_id'])) != plan['project_digest']: raise ValueError('Подача изменилась. Повторите предварительную проверку.')
            if any(r['duplicates'] and not r['allow_duplicate'] for r in plan['rows']): raise ValueError('Выберите явное решение для точного дубликата.')
            result = []
            for row in plan['rows']:
                progress(len(result),len(plan['rows']),'Сохраняем версии')
                if cancel(): raise ValueError('Операция отменена до публикации; прежние материалы сохранены.')
                v = check_version(row['version']); base = self.folder(lib['id'])
                self.no_links(base/'blobs');self.no_links(base/'versions')
                for part in v['parts'].values():
                    src = folder/part['blob']
                    with src.open('rb') as stream:
                        if hash_stream(stream) != {k:part[k] for k in ('sha256','size')}: raise ValueError('Временная часть материала повреждена.')
                    target = base/'blobs'/part['blob']; target.parent.mkdir(exist_ok=True)
                    if target.exists(): self.part(lib['id'],part)
                    else:
                        temp = target.with_suffix('.tmp')
                        shutil.copyfile(src,temp)
                        with temp.open('rb') as stream:
                            if hash_stream(stream) != {k:part[k] for k in ('sha256','size')}: raise ValueError('Ошибка копирования части материала.')
                        os.replace(temp,target)
                path = base/'versions'/f"{v['id']}.json"
                if path.exists() and json_data(path.read_bytes()) != v: raise ValueError('Неизменяемая версия уже существует с другим содержимым.')
                if not path.exists(): atomic_json(path,v)
                item = lib['materials'].setdefault(v['material_id'],{'id':v['material_id'],'hidden':False,'versions':[]})
                item['versions'].append(v['id'])
                item['latest'] = {'metadata':v['metadata'],'created_at':v['created_at'],'names':[x['name'] for x in v['parts'].values()],
                                  'translated':'translation' in v['parts'], 'signature':digest({k:x['sha256'] for k,x in v['parts'].items() if k!='ready' or 'original' not in v['parts']})}
                result.append({'library_id':lib['id'],'material_id':v['material_id'],'version_id':v['id']})
            if cancel(): raise ValueError('Операция отменена до публикации; прежние материалы сохранены.')
            lib['revision'] += 1; lib['operations'][token] = {'materials':result}
            atomic_json(self.folder(lib['id'])/'library.json',lib)
            return lib['operations'][token]

    def import_preview(self, pid, rows, cancel=lambda:False, progress=lambda *args:None):
        self.check_rows(rows)
        with LOCK:
            p = self.store.load(pid); proposed = deepcopy(p); docs = []; errors = []; warnings = []
            for row in rows:
                if cancel(): raise ValueError('Добавление отменено до публикации.')
                progress(len(docs)+len(errors),len(rows),'Проверяем версии и назначения')
                try:
                    v = self.version(row['library_id'],row['material_id'],row['version_id'],verify=True)
                    mode = row.get('mode','prepare'); role = 'original' if mode=='prepare' else 'ready'
                    if mode not in ('prepare','passthrough') or role not in v['parts']: raise ValueError('В выбранной версии нет частей для этого режима.')
                    parts = v['parts']; meta = v['metadata']; original = deepcopy(parts[role]); original.pop('size')
                    translation = deepcopy(parts['translation']) if mode=='prepare' and 'translation' in parts else None
                    if translation: translation.pop('size')
                    d = {'id':uuid4().hex,'title':meta['title'],'short_title':meta['short_title'],'language':meta['language'],
                         'prefix':'','number':None,'designation':'Exhibit','filename':parts[role]['name'],'filename_mode':'manual','folder':'',
                         'mode':mode,'original_label':'[Original]','translation_label':'[Translation]','original':original,
                         'translation':translation,'translation_confirmed':bool(translation),'approved':None,'aliases':[],'style':None,
                         'format_overrides':deepcopy(v['recipe']['format']),
                         'selection':deepcopy(v['recipe']['selection']) if mode=='prepare' else [{'page':i+1} for i in range(original['pages'])],
                         'translation_selection':deepcopy(v['recipe']['translation_selection']) if translation else []}
                    proposed['documents'].append(d)
                    changes = {k:row[k] for k in ('prefix','number','designation','filename','folder') if k in row}
                    self.store.update(proposed,d['id'],changes,persist=False)
                    if translation: d['translation_binding']=binding(d)
                    d['provenance'] = {'schema':1,'library_id':v['library_id'],'material_id':v['material_id'],'version_id':v['id'],
                                       'version_hash':digest(v),'base':{k:deepcopy(d[k]) for k in COPY_FIELDS}}
                    docs.append(d)
                    if mode=='passthrough': warnings.append('Готовый PDF переносится без изменения штампа: '+str(v.get('ready_identifier') or 'без обозначения'))
                except (ValueError,KeyError) as exc: errors.append({'material_id':row.get('material_id'),'message':str(exc)})
            ids, paths = {}, {}
            for d in proposed['documents']:
                ident = unicodedata.normalize('NFC',identifier(d,proposed)).casefold(); path = unicodedata.normalize('NFC',safe_path(d['folder'],d['filename'])).casefold()
                if ident and ident in ids: errors.append({'document_id':d['id'],'message':'Повторяется полный идентификатор: '+identifier(d,proposed)})
                if path in paths: errors.append({'document_id':d['id'],'message':'Повторяется выходной путь: '+path})
                ids[ident]=d['id'];paths[path]=d['id']
            for path in paths:
                if any(parent.as_posix().casefold() in paths for parent in Path(path).parents if str(parent)!='.'):
                    errors.append({'message':'Файл конфликтует с папкой другого приложения.'})
            plan = {'action':'import','project_id':pid,'project_digest':digest(p),'documents':docs,'errors':errors,'warnings':warnings}
            folder = self.pending(); atomic_json(folder/'plan.json',plan)
            return {'token':folder.name,**plan}

    @staticmethod
    def check_rows(rows):
        if not isinstance(rows,list) or not rows or len(rows)>100 or any(not isinstance(row,dict) for row in rows):
            raise ValueError('Выберите от 1 до 100 материалов для одной операции.')

    def import_apply(self, token, cancel=lambda:False, progress=lambda *args:None):
        folder = self.pending(token); plan = json_data((folder/'plan.json').read_bytes())
        if plan.get('action')!='import': raise ValueError('Повторите проверку назначений для добавления в подачу.')
        with LOCK, io_errors():
            p = self.store.load(plan['project_id'])
            if token in p.get('library_operations',{}): return self.store.public(p)
            if plan['errors'] or not plan['documents']: raise ValueError('Исправьте назначения до добавления. Ничего не изменено.')
            if digest(p) != plan['project_digest']: raise ValueError('Подача изменилась в другом окне. Повторите предварительную проверку.')
            # Stage all bytes before changing even the project schema.
            for d in plan['documents']:
                progress(plan['documents'].index(d),len(plan['documents']),'Копируем независимые файлы')
                prov = d['provenance']; v = self.version(prov['library_id'],prov['material_id'],prov['version_id'],verify=True)
                if digest(v) != prov['version_hash']: raise ValueError('Идентичность версии изменилась.')
                for source in (d['original'],d['translation']):
                    if cancel(): raise ValueError('Добавление отменено; подача сохранена.')
                    if source:
                        self.part(prov['library_id'],{**source,'size':next(x['size'] for x in v['parts'].values() if x['blob']==source['blob'])})
                        shutil.copyfile(self.folder(prov['library_id'])/'blobs'/source['blob'],folder/source['blob'])
            if cancel(): raise ValueError('Добавление отменено; подача сохранена.')
            from .schema import upgrade
            p = upgrade(self.store,p,target=2,persist=False)
            proposed = deepcopy(p)
            inputs = self.store.folder(p['id'])/'inputs'; inputs.mkdir(exist_ok=True)
            for d in plan['documents']:
                for source in (d['original'],d['translation']):
                    if source:
                        target = inputs/source['blob']
                        if target.exists(): self.store.source(p,source)
                        else: os.replace(folder/source['blob'],target)
                proposed['documents'].append(deepcopy(d))
            proposed['scanned']=False; proposed['links_reviewed']=False
            proposed.setdefault('library_operations',{})[token] = [d['id'] for d in plan['documents']]
            self.store.save(proposed)
            return self.store.public(proposed)

    def compare(self, lid, mid, before, after, cancel=lambda:False, progress=lambda *args:None):
        progress(0,2,'Проверяем обе версии')
        a=self.version(lid,mid,before,verify=True)
        if cancel(): raise ValueError('Сравнение отменено.')
        b=self.version(lid,mid,after,verify=True); changes=[]
        for role in sorted(ROLES):
            x,y=a['parts'].get(role),b['parts'].get(role)
            changes.append({'field':role,'changed':x!=y,'before':None if x is None else {'pages':x['pages'],'name':x['name']},
                            'after':None if y is None else {'pages':y['pages'],'name':y['name']}})
        for field in FIELDS:
            changes.append({'field':field,'changed':a['metadata'].get(field)!=b['metadata'].get(field),
                            'before':a['metadata'].get(field),'after':b['metadata'].get(field)})
        changes.append({'field':'recipe','changed':a['recipe']!=b['recipe'],'before':a['recipe'],'after':b['recipe']})
        return {'before':a,'after':b,'changes':changes}

    def notifications(self, pid):
        p=self.store.load(pid); results=[]
        for d in p['documents']:
            prov=d.get('provenance')
            if not prov: continue
            result={'document_id':d['id'],'title':d['title'],'identifier':identifier(d,p),'source':deepcopy(prov),
                    'local_changed':any(d.get(k)!=prov['base'][k] for k in COPY_FIELDS)}
            try:
                base=self.version(prov['library_id'],prov['material_id'],prov['version_id'],verify=True)
                if digest(base)!=prov['version_hash']: raise ValueError('ID источника совпадает, но сохранённая версия имеет другое содержание.')
                card=self.load(prov['library_id'])['materials'][prov['material_id']]
                result.update(available=True,versions=card['versions'],newer=card['versions'].index(prov['version_id'])<len(card['versions'])-1,
                              library_name=self.load(prov['library_id'])['name'])
            except (ValueError,OSError,KeyError,TypeError) as exc: result.update(available=False,message=str(exc))
            results.append(result)
        return results

    def update_preview(self, pid, did, version_id, choices=None, cancel=lambda:False, progress=lambda *args:None):
        """Three-way merge; local conflicts require an explicit field decision."""
        choices=choices or {}
        if not isinstance(choices,dict) or not choices.keys()<=set(COPY_FIELDS) or any(v not in ('local','library') for v in choices.values()):
            raise ValueError('Выберите местные данные или данные библиотеки для каждого конфликта.')
        with LOCK:
            p=self.store.load(pid); d=self.store.document(p,did); prov=d.get('provenance')
            if not prov: raise ValueError('У приложения нет библиотечного источника.')
            old=self.version(prov['library_id'],prov['material_id'],prov['version_id'],verify=True)
            if digest(old)!=prov['version_hash']: raise ValueError('Содержание исходной версии не соответствует происхождению.')
            if cancel(): raise ValueError('Обновление отменено.')
            v=self.version(prov['library_id'],prov['material_id'],version_id,verify=True)
            progress(1,2,'Сравниваем местную копию с выбранной версией')
            role='original' if d['mode']=='prepare' else 'ready'
            if role not in v['parts']: raise ValueError('В версии нет части для текущего режима приложения.')
            parts=deepcopy(v['parts'])
            for part in parts.values(): part.pop('size',None)
            incoming={**{k:v['metadata'][k] for k in ('title','short_title','language')},
                      'original':parts[role],'translation':parts.get('translation') if role=='original' else None,
                      'selection':deepcopy(v['recipe']['selection']) if role=='original' else [{'page':i+1} for i in range(parts[role]['pages'])],
                      'translation_selection':deepcopy(v['recipe']['translation_selection']) if role=='original' and 'translation' in parts else [],
                      'format_overrides':deepcopy(v['recipe']['format']), 'translation_confirmed':role=='original' and 'translation' in parts}
            changed=[]; conflicts=[]; proposed=deepcopy(d)
            for key in COPY_FIELDS:
                base,local,remote=prov['base'][key],d.get(key),incoming[key]
                is_local=local!=base; is_remote=remote!=base
                conflict=is_local and local!=remote and is_remote
                if is_local or is_remote:
                    changed.append({'field':key,'local_changed':is_local,'library_changed':is_remote,'conflict':conflict,
                                    'local':local,'library':remote})
                if conflict and key not in choices: conflicts.append(key)
                proposed[key]=deepcopy(local if choices.get(key)=='local' or is_local and choices.get(key)!='library' else remote)
            errors=[]
            try:
                pdf.validate_selection(proposed['selection'],proposed['original']['pages'])
                if proposed['translation']: pdf.validate_selection(proposed['translation_selection'],proposed['translation']['pages'])
                check_format(proposed['format_overrides'])
            except ValueError as exc: errors.append(str(exc))
            # A retained translation can only stay confirmed for its exact original and selection.
            if proposed['translation']:
                exact=binding(proposed)
                confirmed=(translation_valid(d) and exact==binding(d)) or incoming['translation_confirmed'] and exact==v['translation_binding']
                if choices.get('translation_confirmed')=='local' and not d['translation_confirmed']: confirmed=False
                proposed['translation_confirmed']=bool(confirmed)
                proposed['translation_binding']=exact if confirmed else None
            else: proposed['translation_confirmed']=False; proposed.pop('translation_binding',None)
            from .project import revision
            needs_review=revision(p,proposed)!=revision(p,d)
            if needs_review: proposed['approved']=None
            proposed['provenance']={'schema':1,'library_id':v['library_id'],'material_id':v['material_id'],'version_id':v['id'],
                                    'version_hash':digest(v),'base':incoming}
            plan={'action':'update','project_id':pid,'project_digest':digest(p),'document':proposed,'version_hash':digest(v),
                  'changes':changed,'conflicts':conflicts,'errors':errors,'needs_review':needs_review,
                  'translation_review':bool(proposed['translation'] and not proposed['translation_confirmed']),
                  'links_review':any(proposed.get(k)!=d.get(k) for k in ('title','short_title'))}
            folder=self.pending(); atomic_json(folder/'plan.json',plan)
            return {'token':folder.name,**plan}

    def update_apply(self, token, cancel=lambda:False, progress=lambda *args:None):
        from .backup import save_archive
        folder=self.pending(token); plan=json_data((folder/'plan.json').read_bytes())
        if plan.get('action')!='update': raise ValueError('Повторите сравнение для обновления приложения.')
        with LOCK, io_errors():
            p=self.store.load(plan['project_id'])
            if token in p.get('library_operations',{}): return self.store.public(p)
            if plan['conflicts'] or plan['errors']: raise ValueError('Разрешите конфликты и повторите сравнение; подача не изменена.')
            if digest(p)!=plan['project_digest']: raise ValueError('Подача изменилась. Повторите сравнение.')
            d=plan['document']; prov=d['provenance']; v=self.version(prov['library_id'],prov['material_id'],prov['version_id'],verify=True)
            if digest(v)!=plan['version_hash']: raise ValueError('Выбранная версия изменилась; обновление отменено.')
            if cancel(): raise ValueError('Обновление отменено; подача сохранена.')
            backups=self.store.root/'backups'; backups.mkdir(exist_ok=True)
            snapshot=backups/('before-material-update-'+token+'.zip')
            if not snapshot.exists(): save_archive(self.store,p['id'],snapshot)
            from .backup import inspect_archive
            checked=inspect_archive(snapshot)
            if digest(checked['project'])!=digest(p): raise ValueError('Снимок не соответствует подаче. Повторите сравнение.')
            try:
                for source in (d['original'],d['translation']):
                    if cancel(): raise ValueError('Обновление отменено; подача сохранена.')
                    if source:
                        target=self.store.folder(p['id'])/'inputs'/source['blob']
                        if target.exists(): self.store.source(p,source)
                        else:
                            part=next(x for x in v['parts'].values() if x['blob']==source['blob'])
                            shutil.copyfile(self.part(prov['library_id'],part),folder/source['blob'])
                            with (folder/source['blob']).open('rb') as stream:
                                if hash_stream(stream)!={k:part[k] for k in ('size','sha256')}: raise ValueError('Ошибка копирования части материала.')
                            os.replace(folder/source['blob'],target)
                if cancel(): raise ValueError('Обновление отменено; подача сохранена.')
                proposed=deepcopy(p)
                proposed['documents']=[deepcopy(d) if x['id']==d['id'] else x for x in proposed['documents']]
                if plan['links_review']: proposed['scanned']=False; proposed['links_reviewed']=False
                proposed.setdefault('library_operations',{})[token]={'snapshot':'backups/'+snapshot.name}
                self.store.save(proposed)
            except Exception as exc: raise ValueError(f'Обновление не применено. Подача сохранена; снимок: backups/{snapshot.name}. {exc}') from exc
            return self.store.public(proposed)

    def archive_save(self, lid, cancel=lambda:False, progress=lambda *args:None):
        from .library_backup import save
        folder=self.pending();checked=save(self,lid,folder/'library.zip',cancel,progress)
        return {'token':folder.name,'name':checked['library']['name'],'summary':checked['summary']}

    def archive_preview(self, token, cancel=lambda:False, progress=lambda *args:None):
        from .library_backup import inspect
        folder=self.pending(token);checked=inspect(folder/'library.zip',cancel,progress)
        with (folder/'library.zip').open('rb') as stream: fingerprint=hash_stream(stream)['sha256']
        atomic_json(folder/'archive-check.json',{'sha256':fingerprint})
        lib=checked['library']
        return {'token':token,'id':lib['id'],'name':lib['name'],'summary':checked['summary'],'conflict':self.folder(lib['id']).exists()}

    def archive_restore(self, token, copy=False, cancel=lambda:False, progress=lambda *args:None):
        from .library_backup import restore
        folder=self.pending(token)
        with (folder/'library.zip').open('rb') as f: actual=hash_stream(f)['sha256']
        if actual!=json_data((folder/'archive-check.json').read_bytes())['sha256']: raise ValueError('Архив изменился. Повторите проверку.')
        result_path=folder/('restore-copy.json' if copy else 'restore-original.json')
        if result_path.exists(): return json_data(result_path.read_bytes())
        result=restore(self,folder/'library.zip',copy,cancel,progress,identity_seed=token)
        atomic_json(result_path,result)
        return result

    def upload_preview(self, lid, rows, cancel=lambda:False, progress=lambda *args:None):
        self.check_rows(rows)
        with LOCK,io_errors():
            lib=self.load(lid);folder=self.pending();planned=[];errors=[]
            tokens=[r.get('token') for r in rows]
            if len(set(tokens))!=len(tokens): raise ValueError('Один загруженный файл указан несколько раз.')
            primaries={r['token']:r for r in rows if r.get('role') in ('original','ready')}
            for row in rows:
                if row.get('role') not in ROLES: errors.append({'token':row.get('token'),'message':'Укажите роль каждого PDF.'})
                if row.get('role')=='translation' and (row.get('pair') not in primaries or primaries[row['pair']]['role']!='original'):
                    errors.append({'token':row['token'],'message':'Явно выберите оригинал для перевода.'})
            for token,row in primaries.items():
                progress(len(planned),len(primaries),'Проверяем роли и состав PDF')
                if cancel(): raise ValueError('Наполнение отменено до сохранения.')
                try:
                    parts={};pair=[r for r in rows if r.get('role')=='translation' and r.get('pair')==token]
                    if len(pair)>1: raise ValueError('К оригиналу можно привязать один перевод.')
                    if pair and not pair[0].get('confirmed'): raise ValueError('Просмотрите перевод и явно подтвердите его соответствие выбранному оригиналу.')
                    for r in [row,*pair]:
                        upload=self.pending(r['token']);part=json_data((upload/'part.json').read_bytes())
                        with (upload/part['blob']).open('rb') as f:
                            if hash_stream(f)!={k:part[k] for k in ('sha256','size')}: raise ValueError('Загруженный PDF повреждён. Выберите его заново.')
                        shutil.copyfile(upload/part['blob'],folder/part['blob']);parts[r['role']]=part
                    selections={k:[{'page':i+1} for i in range(p['pages'])] for k,p in parts.items()}
                    from .project import DEFAULT_STYLE,DEFAULT_LABELS,NEW_LAYOUT
                    recipe={'selection':selections.get('original',[]),'translation_selection':selections.get('translation',[]),'format':{**DEFAULT_STYLE,**DEFAULT_LABELS,**NEW_LAYOUT}}
                    b={'original':parts['original']['sha256'],'translation':parts['translation']['sha256'],'selection':digest(recipe['selection']),'translation_selection':digest(recipe['translation_selection'])} if pair else None
                    mid=row.get('material_id') or uuid4().hex;uid(mid)
                    if row.get('material_id') and mid not in lib['materials']: raise ValueError('Материал для версии не найден.')
                    v={'schema':SCHEMA,'id':uuid4().hex,'library_id':lid,'material_id':mid,'parent':lib['materials'][mid]['versions'][-1] if mid in lib['materials'] else None,
                       'created_at':datetime.now(timezone.utc).isoformat(),'comment':clean_text(row.get('comment',''),1000),'metadata':metadata(row['metadata']),
                       'parts':parts,'recipe':recipe,'translation_binding':b,'ready_identifier':clean_text(row.get('ready_identifier','')) if row['role']=='ready' else None}
                    check_version(v)
                    signature=digest({k:x['sha256'] for k,x in parts.items() if k!='ready' or 'original' not in parts})
                    duplicates=[m['id'] for m in lib['materials'].values() if m['latest']['signature']==signature]
                    duplicates += [r['version']['material_id'] for r in planned if digest({k:x['sha256'] for k,x in r['version']['parts'].items() if k!='ready' or 'original' not in r['version']['parts']})==signature]
                    planned.append({'version':v,'duplicates':duplicates,'allow_duplicate':bool(row.get('allow_duplicate'))})
                except (ValueError,KeyError) as exc: errors.append({'token':token,'message':str(exc)})
            plan={'action':'save','library_id':lid,'revision':lib['revision'],'rows':planned,'errors':errors,'warnings':[]}
            atomic_json(folder/'plan.json',plan)
            return {'token':folder.name,**plan}

    def hide(self, lid, mid, hidden, revision):
        if type(hidden) is not bool: raise ValueError('Укажите скрытие или возврат материала.')
        with LOCK,io_errors():
            lib=self.load(lid)
            if lib['revision']!=revision: raise ValueError('Каталог изменился. Обновите его и повторите действие.')
            card=lib['materials'].get(uid(mid))
            if not card: raise ValueError('Материал не найден.')
            card['hidden']=hidden;lib['revision']+=1
            atomic_json(self.folder(lid)/'library.json',lib)
            return {'id':mid,'hidden':hidden,'revision':lib['revision']}

    def usage(self, lid, mid):
        """Rebuilt from authoritative local project files; no persistent index to stale."""
        lib=self.load(lid);card=lib['materials'].get(uid(mid))
        if not card: raise ValueError('Материал не найден.')
        results=[];errors=[]
        with LOCK:
            for folder in sorted(self.store.root.iterdir()):
                if not ID.fullmatch(folder.name) or not folder.is_dir(): continue
                try:
                    p=self.store.load(folder.name)
                    for d in p['documents']:
                        source=d.get('provenance')
                        if not source or (source.get('library_id'),source.get('material_id'))!=(lid,mid): continue
                        v=self.version(lid,mid,source['version_id'],verify=True)
                        valid=digest(v)==source['version_hash']
                        results.append({'project_id':p['id'],'name':p['name'],'document_id':d['id'],'identifier':identifier(d,p),'version_id':source['version_id'],
                                        'version_number':card['versions'].index(source['version_id'])+1,'newer':valid and source['version_id']!=card['versions'][-1],
                                        'identity_valid':valid,'local_changed':any(d.get(k)!=source['base'][k] for k in COPY_FIELDS)})
                except (ValueError,OSError,KeyError,TypeError,json.JSONDecodeError) as exc: errors.append({'project_id':folder.name,'message':'Подача недоступна или повреждена: '+str(exc)})
        return {'items':results,'errors':errors,'scope':'Только подачи в папке данных этого приложения. Поданные архивы и другие компьютеры не входят в справку.'}
