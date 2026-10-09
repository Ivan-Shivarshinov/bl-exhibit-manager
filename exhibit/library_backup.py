"""Bounded streaming library archives; never extract arbitrary ZIP members."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4,uuid5,NAMESPACE_URL
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile
import json
import os
import re
import shutil
import stat
import unicodedata
from hashlib import sha256

from .backup import MAX_BYTES, MAX_FILES, MAX_JSON, MAX_INPUT, CHUNK, json_data,hash_stream,io_errors
from .library import SCHEMA,uid,metadata,check_version,atomic_json
from .project import LOCK, digest

MEMBER=re.compile(r'library.json|versions/[a-f0-9]{32}\.json|blobs/[a-f0-9]{64}\.pdf')

def transfer(stream,output=None,cancel=lambda:False):
    h=sha256();size=0
    while chunk:=stream.read(CHUNK):
        if cancel(): raise ValueError('Операция архива отменена; прежние данные сохранены.')
        size+=len(chunk)
        if size>MAX_BYTES: raise ValueError('Архив превышает 8 ГБ.')
        h.update(chunk)
        if output is not None: output.write(chunk)
    return {'sha256':h.hexdigest(),'size':size}


def inspect(path, cancel=lambda:False, progress=lambda *args:None):
    try:
        with ZipFile(path) as z:
            infos=z.infolist();names=set();total=0
            if len(infos)>MAX_FILES+1: raise ValueError('В архиве больше 20 000 записей.')
            for i in infos:
                normal=unicodedata.normalize('NFC',i.filename).casefold()
                if normal in names or i.is_dir() or i.flag_bits&1 or stat.S_IFMT(i.external_attr>>16) not in (0,stat.S_IFREG) or i.filename!='backup.json' and not MEMBER.fullmatch(i.filename):
                    raise ValueError('Недопустимый путь, ссылка или повтор файла в архиве библиотеки.')
                names.add(normal);total+=i.file_size
                if total>MAX_BYTES: raise ValueError('Распакованный архив превышает 8 ГБ.')
                if i.filename.endswith('.json') and i.file_size>MAX_JSON or i.filename.startswith('blobs/') and i.file_size>MAX_INPUT:
                    raise ValueError('Часть архива превышает допустимый размер.')
            if 'backup.json' not in names or 'library.json' not in names: raise ValueError('Это не архив библиотеки Exhibit Manager.')
            manifest=json_data(z.read('backup.json'))
            if manifest.get('application')!='exhibit-manager-library' or type(manifest.get('format')) is not int or manifest['format']!=1:
                raise ValueError('Формат архива библиотеки не поддерживается.')
            files=manifest.get('files')
            if not isinstance(files,dict) or set(files)!=names-{'backup.json'}: raise ValueError('Состав архива не соответствует манифесту.')
            for n,(name,expected) in enumerate(files.items()):
                if cancel(): raise ValueError('Проверка архива отменена.')
                progress(n,len(files),'Проверяем архив библиотеки')
                with z.open(name) as f: actual=transfer(f,cancel=cancel)
                if actual!=expected: raise ValueError('Повреждён файл архива библиотеки: '+name)
            lib=json_data(z.read('library.json'))
            if type(lib.get('schema')) is not int or lib['schema']!=SCHEMA: raise ValueError('Схема библиотеки не поддерживается.')
            uid(lib['id']);metadata({'title':lib['name']})
            if type(lib.get('revision')) is not int or not isinstance(lib.get('materials'),dict) or lib.get('operations')!={}: raise ValueError('Неполные сведения библиотеки.')
            required={'library.json'};versions=set();pdf_info={}
            for mid,card in lib['materials'].items():
                uid(mid)
                if card['id']!=mid or type(card['hidden']) is not bool or not card['versions'] or len(set(card['versions']))!=len(card['versions']): raise ValueError('Повреждён список версий материала.')
                parent=None
                for vid in card['versions']:
                    uid(vid); name=f'versions/{vid}.json'; required.add(name)
                    if vid in versions: raise ValueError('Версия повторяется в другом материале.')
                    v=check_version(json_data(z.read(name)))
                    if (v['id'],v['material_id'],v['library_id'],v['parent'])!=(vid,mid,lib['id'],parent): raise ValueError('Нарушена идентичность или порядок версий.')
                    parent=vid;versions.add(vid)
                    for part in v['parts'].values():
                        name='blobs/'+part['blob'];required.add(name)
                        if files.get(name)!={k:part[k] for k in ('sha256','size')}: raise ValueError('Отсутствует обязательная часть версии.')
                        if name not in pdf_info:
                            if cancel(): raise ValueError('Проверка архива отменена.')
                            from .pdf import inspect_pdf
                            pdf_info[name]=digest(inspect_pdf(z.read(name)))
                        if digest({k:part.get(k) for k in ('pages','sizes','text_pages')})!=pdf_info[name]: raise ValueError('Сведения о страницах не соответствуют PDF в архиве.')
                if card['latest']!=summary(v): raise ValueError('Каталог не соответствует последней версии.')
            if required!=set(files): raise ValueError('В архиве есть посторонние или отсутствующие обязательные файлы.')
            return {'library':lib,'versions':sorted(versions),'files':files,'summary':{'materials':len(lib['materials']),'versions':len(versions),'files':len(files),'bytes':total},'created_at':manifest.get('created_at')}
    except (BadZipFile,EOFError,RuntimeError,NotImplementedError,KeyError,TypeError,AttributeError,RecursionError) as exc:
        raise ValueError('Архив библиотеки повреждён или имеет неподдерживаемую структуру.') from exc


def summary(v):
    from .project import digest
    return {'metadata':v['metadata'],'created_at':v['created_at'],'names':[p['name'] for p in v['parts'].values()],
            'translated':'translation' in v['parts'],'signature':digest({k:x['sha256'] for k,x in v['parts'].items() if k!='ready' or 'original' not in v['parts']})}


def save(library,lid,destination,cancel=lambda:False,progress=lambda *args:None):
    with LOCK,io_errors():
        lib=library.load(lid);lib['operations']={};members={'library.json':json.dumps(lib,ensure_ascii=False).encode()}
        for mid,card in lib['materials'].items():
            for vid in card['versions']:
                v=library.version(lid,mid,vid,_catalog=lib)
                members[f'versions/{vid}.json']=library.folder(lid)/'versions'/f'{vid}.json'
                for p in v['parts'].values(): members['blobs/'+p['blob']]=library.part(lid,p)
        if len(members)>MAX_FILES or sum(len(v) if isinstance(v,bytes) else v.stat().st_size for v in members.values())>MAX_BYTES:
            raise ValueError('Библиотека превышает предел архива: 8 ГБ или 20 000 файлов.')
        archive_file=Path(destination).open('xb')
        try:
            files={}
            with archive_file,ZipFile(archive_file,'w',ZIP_DEFLATED,allowZip64=True) as z:
                from io import BytesIO
                for n,(name,value) in enumerate(members.items()):
                    if cancel(): raise ValueError('Сохранение архива отменено.')
                    progress(n,len(members),'Сохраняем архив библиотеки')
                    with (BytesIO(value) if isinstance(value,bytes) else value.open('rb')) as src,z.open(name,'w',force_zip64=True) as out:
                        files[name]=transfer(src,out,cancel)
                z.writestr('backup.json',json.dumps({'application':'exhibit-manager-library','format':1,'created_at':datetime.now(timezone.utc).isoformat(),'files':files},ensure_ascii=False))
            return inspect(destination,cancel,progress)
        except Exception:
            Path(destination).unlink(missing_ok=True);raise


def restore(library,path,copy=False,cancel=lambda:False,progress=lambda *args:None,identity_seed=None):
    with LOCK,io_errors():
        checked=inspect(path,cancel,progress);lib=deepcopy(checked['library'])
        if library.folder(lib['id']).exists() and not copy: raise ValueError('Библиотека уже существует. Отмените действие или восстановите независимую копию.')
        if shutil.disk_usage(library.store.root).free<checked['summary']['bytes']+16_000_000: raise ValueError('Недостаточно места для восстановления библиотеки.')
        versions=checked['versions']
        def mapped(v):
            if not copy: return v
            return {**v,'id':vids[v['id']],'material_id':mids[v['material_id']],'library_id':lib['id'],'parent':vids[v['parent']] if v['parent'] else None}
        def read_version(z,vid):
            name=f'versions/{vid}.json';data=z.read(name)
            if {'sha256':sha256(data).hexdigest(),'size':len(data)}!=checked['files'][name]: raise ValueError('Архив изменился во время восстановления.')
            return mapped(check_version(json_data(data)))
        if copy:
            def new_id(key): return uuid5(NAMESPACE_URL,identity_seed+key).hex if identity_seed else uuid4().hex
            lib['id']=new_id('library'+lib['id']);lib['name']=lib['name'][:110]+' (копия)'
            mids={m:new_id('material'+m) for m in lib['materials']};vids={v:new_id('version'+v) for v in versions}
            lib['materials']={mids[m]:{**card,'id':mids[m],'versions':[vids[v] for v in card['versions']]} for m,card in lib['materials'].items()}
            if library.folder(lib['id']).exists() and identity_seed:
                if library.load(lib['id'])!=lib: raise ValueError('Ранее восстановленная копия уже изменена; повтор не применён.')
                with ZipFile(path) as z:
                    for vid in versions:
                        v=read_version(z,vid)
                        if library.version(lib['id'],v['material_id'],v['id'],verify=True)!=v: raise ValueError('Ранее восстановленная копия повреждена.')
                return {'id':lib['id'],'name':lib['name'],'summary':checked['summary']}
        library.root.mkdir(exist_ok=True)
        with TemporaryDirectory(prefix='.restore-',dir=library.root) as tmp:
            staging=Path(tmp)/'library';staging.mkdir();(staging/'blobs').mkdir();(staging/'versions').mkdir()
            with ZipFile(path) as z:
                for n,(name,expected) in enumerate(checked['files'].items()):
                    if cancel(): raise ValueError('Восстановление отменено; прежние библиотеки сохранены.')
                    progress(n,len(checked['files']),'Восстанавливаем библиотеку')
                    if not name.startswith('blobs/'): continue
                    with z.open(name) as src,(staging/name).open('xb') as out:
                        actual=transfer(src,out,cancel);out.flush();os.fsync(out.fileno())
                    if actual!=expected: raise ValueError('Архив изменился во время восстановления.')
                for vid in versions:
                    if cancel(): raise ValueError('Восстановление отменено; прежние библиотеки сохранены.')
                    v=read_version(z,vid);atomic_json(staging/'versions'/(v['id']+'.json'),v)
            atomic_json(staging/'library.json',lib)
            if cancel(): raise ValueError('Восстановление отменено; прежние библиотеки сохранены.')
            target=library.folder(lib['id'])
            if target.exists(): raise ValueError('Библиотека появилась в другом окне. Повторите проверку.')
            os.replace(staging,target)
        return {'id':lib['id'],'name':lib['name'],'summary':checked['summary']}
