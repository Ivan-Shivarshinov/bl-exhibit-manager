"""Versioned, bounded project archives. Never extract user-controlled ZIP paths."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile
import json
import os
import re
import shutil
import stat
import tempfile
import time
import unicodedata

from .project import LOCK

FORMAT = 1
MAX_BYTES = 8_000_000_000
MAX_FILES = 20_000
MAX_JSON = 20_000_000
MAX_INPUT = 50_000_000
CHUNK = 1024 * 1024
ID = r'[a-f0-9]{32}'
BLOB = r'[a-f0-9]{64}\.(?:pdf|docx)'
MEMBER = re.compile(rf'project.json|inputs/{BLOB}|translation-(?:source-)?{ID}\.json|exports/{ID}/(?:manifest.json|Submission.zip)')


def fail(message):
    raise ValueError(message)


def json_data(data):
    if len(data) > MAX_JSON: fail('Слишком большой файл настроек проекта.')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: fail('Повторяющийся ключ в настройках архива.')
            result[key] = value
        return result
    try: return json.loads(data, object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Повреждён файл настроек архива.') from exc


def hash_stream(stream, output=None):
    digest, size = sha256(), 0
    while chunk := stream.read(CHUNK):
        size += len(chunk)
        if size > MAX_BYTES: fail('Архив превышает предел 8 ГБ.')
        digest.update(chunk)
        if output is not None: output.write(chunk)
    return {'size': size, 'sha256': digest.hexdigest()}


def check_project(p, files, read):
    from .schema import check_version
    from .project import check_style, effective_format, identifier, document_ready
    if not isinstance(p, dict): fail('Некорректные настройки проекта.')
    check_version(p.get('schema'))
    if not re.fullmatch(ID, str(p.get('id', ''))) or not isinstance(p.get('name'), str) or not p['name'].strip():
        fail('Некорректное имя или идентификатор проекта.')
    if not isinstance(p.get('documents'), list) or not isinstance(p.get('references'), list) or not isinstance(p.get('style'), dict):
        fail('В архиве отсутствуют настройки проекта.')
    if 'main' not in p or not isinstance(p.get('footnotes'), list) or type(p.get('links_reviewed')) is not bool:
        fail('Неполные настройки проекта.')
    check_style(p['style'])
    ids = [d.get('id') for d in p['documents'] if isinstance(d, dict)]
    if len(ids) != len(p['documents']) or len(set(ids)) != len(ids) or any(not isinstance(i, str) or not re.fullmatch(ID, i) for i in ids):
        fail('Некорректные идентификаторы документов.')
    def source(value):
        if not value: return
        if not isinstance(value, dict) or not re.fullmatch(BLOB, str(value.get('blob', ''))): fail('Некорректная ссылка на исходник.')
        entry = files.get('inputs/'+value['blob'])
        if not entry or entry['sha256'] != value.get('sha256') or value['blob'].split('.')[0] != entry['sha256']:
            fail('В архиве отсутствует или повреждён обязательный исходник.')
    source(p.get('main'))
    for doc in p['documents']:
        required_text = ('title', 'prefix', 'designation', 'filename', 'folder', 'language', 'original_label', 'translation_label')
        if any(not isinstance(doc.get(k), str) for k in required_text) or doc.get('mode') not in ('prepare', 'passthrough'):
            fail('Повреждены настройки документа.')
        if any(not isinstance(doc.get(k), list) for k in ('selection', 'translation_selection', 'aliases')) or 'approved' not in doc or 'translation' not in doc:
            fail('Неполные настройки документа.')
        if doc.get('number') is not None and type(doc['number']) is not int: fail('Некорректный номер документа.')
        try: effective_format(p, doc); identifier(doc, p); document_ready(p, doc)
        except (KeyError, TypeError, AttributeError) as exc: raise ValueError('Некорректное оформление документа.') from exc
        if not doc.get('original'): fail('В архиве отсутствует оригинал документа.')
        source(doc['original']); source(doc.get('translation'))
    for ref in p['references']:
        if not isinstance(ref, dict) or (ref.get('target') is not None and ref['target'] not in ids): fail('Ссылка указывает на отсутствующий документ.')
        if not {'key', 'target', 'mention', 'footnote'} <= ref.keys(): fail('Неполные данные ссылки.')
    # Exercise the same metadata projection the UI opens, without filesystem access.
    # This rejects incomplete nested settings before publishing any restored folder.
    from .project import Store
    class MetadataView(Store):
        def __init__(self): pass
        def source(self, project, value):
            source(value)
            return b''
    try: MetadataView().public(p)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError('Неполные или некорректные настройки проекта в архиве.') from exc
    histories = set()
    for name, entry in files.items():
        if name.startswith('inputs/') and name.split('/')[1].split('.')[0] != entry['sha256']:
            fail('Контрольная сумма исходника не совпадает с именем.')
        if name.startswith('translation-'):
            value = json_data(read(name))
            if not isinstance(value, dict): fail('Повреждены данные перевода.')
            source_hash = value.get('hash') if name.startswith('translation-source-') else value.get('source_hash')
            if 'inputs/'+str(source_hash)+'.pdf' not in files: fail('Отсутствует исходник сохранённого перевода.')
        if name.startswith('exports/'):
            histories.add(name.split('/')[1])
    for eid in histories:
        meta_name, zip_name = f'exports/{eid}/manifest.json', f'exports/{eid}/Submission.zip'
        if meta_name not in files or zip_name not in files: fail('История комплектов неполна.')
        meta = json_data(read(meta_name))
        if meta.get('id') != eid or any(meta.get(k) != files[zip_name][k] for k in ('size', 'sha256')):
            fail('Сохранённый комплект повреждён.')
    return {'documents': len(ids), 'inputs': sum(n.startswith('inputs/') for n in files),
            'translations': sum(n.startswith('translation-') and not n.startswith('translation-source-') for n in files),
            'exports': len(histories), 'files': len(files), 'bytes': sum(v['size'] for v in files.values())}


def inspect_archive(path):
    """Read only; verify all bytes and relationships before offering restore."""
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES + 1: fail('В архиве больше 20 000 файлов.')
            names, total = set(), 0
            for info in infos:
                name = info.filename
                normal = unicodedata.normalize('NFC', name).casefold()
                mode = info.external_attr >> 16
                if (len(name) > 200 or normal in names or info.is_dir() or info.flag_bits & 1
                        or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                        or (name != 'backup.json' and not MEMBER.fullmatch(name))):
                    fail('Архив содержит недопустимый путь, ссылку или повтор файла.')
                names.add(normal); total += info.file_size
                if total > MAX_BYTES: fail('Распакованный архив превышает предел 8 ГБ.')
                if name.endswith('.json') and info.file_size > MAX_JSON: fail('Слишком большой файл настроек проекта.')
                if name.startswith('inputs/') and info.file_size > MAX_INPUT: fail('Исходный PDF или DOCX превышает предел 50 МБ.')
            if 'backup.json' not in names or 'project.json' not in names: fail('Это не архив проекта Exhibit Manager.')
            manifest = json_data(archive.read('backup.json'))
            if not isinstance(manifest, dict) or (type(manifest.get('format')) is not int or manifest.get('format') != FORMAT) or manifest.get('application') != 'exhibit-manager-project':
                fail('Формат архива не поддерживается. Обновите приложение; файлы не изменены.')
            files = manifest.get('files')
            if not isinstance(files, dict) or set(files) != {i.filename for i in infos} - {'backup.json'}:
                fail('Состав архива не совпадает с манифестом.')
            for name, expected in files.items():
                with archive.open(name) as stream: actual = hash_stream(stream)
                if actual != expected: fail('Контрольная сумма или размер файла не совпадает: '+name)
            p = json_data(archive.read('project.json'))
            summary = check_project(p, files, archive.read)
            return {'project': p, 'files': files, 'summary': summary, 'created_at': manifest.get('created_at')}
    except (BadZipFile, EOFError, RuntimeError, NotImplementedError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise ValueError('Архив проекта повреждён или имеет неподдерживаемую структуру.') from exc


def inventory(folder):
    if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
        fail('Папка проекта является ссылкой файловой системы.')
    result = []
    for path in folder.rglob('*'):
        name = path.relative_to(folder).as_posix()
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()): fail('В папке проекта обнаружена ссылка файловой системы.')
        if path.is_dir(): continue
        if MEMBER.fullmatch(name): result.append((name, path))
        elif name.startswith('main-pdf/') or name.endswith('.tmp') or name.startswith('exports/.pending-'):
            continue
        else: fail('Неизвестный файл в папке проекта; архив не создан: '+name)
    if len(result) > MAX_FILES or sum(p.stat().st_size for _, p in result) > MAX_BYTES:
        fail('Проект превышает предел архива: 8 ГБ или 20 000 файлов.')
    return sorted(result)


@contextmanager
def io_errors():
    try: yield
    except OSError as exc:
        raise ValueError('Не удалось записать архив или проект. Проверьте свободное место и доступ к папке; существующие проекты сохранены.') from exc


def save_archive(store, pid, destination, active=()):
    destination = Path(destination)
    with LOCK, io_errors():
        if any(key[0] == pid for key in active): fail('В проекте идёт перевод. Дождитесь завершения или остановите его перед сохранением.')
        store.load(pid)
        if destination.exists(): fail('Файл архива уже существует. Выберите другое имя.')
        members = inventory(store.folder(pid))
        size = sum(path.stat().st_size for _, path in members)
        if shutil.disk_usage(destination.parent).free < size + 16_000_000: fail('Недостаточно свободного места для архива проекта.')
        try:
            files = {}
            with ZipFile(destination, 'x', ZIP_DEFLATED, allowZip64=True) as archive:
                for name, path in members:
                    with path.open('rb') as src, archive.open(name, 'w', force_zip64=True) as out:
                        files[name] = hash_stream(src, out)
                archive.writestr('backup.json', json.dumps({'application': 'exhibit-manager-project', 'format': FORMAT,
                    'created_at': datetime.now(timezone.utc).isoformat(), 'files': files}, ensure_ascii=False))
            inspect_archive(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    return destination


def restore_archive(store, path, copy=False):
    with LOCK, io_errors():
        checked = inspect_archive(path)
        p = deepcopy(checked['project'])
        conflict = store.folder(p['id']).exists()
        if conflict and not copy: fail('Проект уже существует. Выберите «Восстановить копию» либо отмените действие.')
        if copy:
            old_id = p['id']; p['id'] = uuid4().hex
            p['name'] = p['name'][:110] + ' (копия)'
            bindings = p.setdefault('restored_word_bindings', {})
            # Bind exact marker identities from each immutable DOCX, never all old-project links.
            from . import word, word_bridge
            with ZipFile(path) as archive:
                for name in checked['files']:
                    if name.startswith('inputs/') and name.endswith('.docx'):
                        parts = word.package(archive.read(name))
                        markers = bindings.setdefault(name.split('/')[1].split('.')[0], [])
                        for rel in word.xml(parts[word.RELS]) if word.RELS in parts else []:
                            meta = word_bridge.parse_link(rel.get('Target', ''))
                            if meta and meta['project'] == old_id and meta not in markers: markers.append(meta)
        if shutil.disk_usage(store.root).free < checked['summary']['bytes'] + 16_000_000: fail('Недостаточно свободного места для восстановления проекта.')
        with tempfile.TemporaryDirectory(prefix='.restore-', dir=store.root) as temp:
            staging = Path(temp) / 'project'; staging.mkdir()
            with ZipFile(path) as archive:
                for name, expected in checked['files'].items():
                    target = staging / name; target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(name) as src, target.open('xb') as out:
                        actual = hash_stream(src, out); out.flush(); os.fsync(out.fileno())
                    if actual != expected: fail('Архив изменился во время восстановления.')
            if copy:
                with (staging/'project.json').open('w', encoding='utf-8') as out:
                    json.dump(p, out, ensure_ascii=False, indent=2); out.flush(); os.fsync(out.fileno())
            from .schema import upgrade
            from .project import Store
            class StagedStore(Store):
                def folder(self, pid): return staging
            p = upgrade(StagedStore(store.root), p)
            target = store.folder(p['id'])
            if target.exists(): fail('Проект уже появился на диске. Повторите проверку архива.')
            staging.rename(target)
        return p


def work_folder(store, token=None):
    parent = store.root / '.backup-work'; parent.mkdir(exist_ok=True)
    if token is not None:
        if not isinstance(token, str) or not re.fullmatch(ID, token): fail('Некорректный архив.')
        folder = parent/token
        if not folder.is_dir(): fail('Временный архив уже удалён. Выберите файл заново.')
        return folder
    # Abandoned uploads after a closed browser/server restart expire in 24 hours.
    for folder in parent.iterdir():
        if re.fullmatch(ID, folder.name) and folder.is_dir() and time.time()-folder.stat().st_mtime > 86400:
            shutil.rmtree(folder)
    folder = parent/uuid4().hex; folder.mkdir()
    return folder


def preview(store, path):
    checked = inspect_archive(path)
    p = checked['project']
    return {'name': p['name'], 'id': p['id'], 'schema': p['schema'], 'summary': checked['summary'],
            'conflict': store.folder(p['id']).exists(), 'created_at': checked['created_at']}
