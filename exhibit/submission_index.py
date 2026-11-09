"""One checked, bounded PDF snapshot for preview, index and export."""
from contextlib import contextmanager
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from zipfile import ZipFile, ZIP_DEFLATED
import re
import shutil
import unicodedata

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from . import index_settings
from .project import LOCK, digest, identifier, safe_path, effective_document
from .backup import io_errors

MAX_BYTES = 8_000_000_000
STALE = 'Подача или настройка изменились. Обновите перечень приложений.'


def natural(value):
    parts = re.split(r'(\d+)', unicodedata.normalize('NFC', value).casefold())
    return tuple((1, int(p)) if p.isdecimal() else (0, p) for p in parts)


def fingerprint(project):
    # History publication and operation receipts do not affect delivered bytes.
    return digest({k:v for k,v in project.items() if k not in ('schema_backup','library_operations')})


def content_key(project):
    return digest([effective_document(project,d) for d in project['documents']])


def ordered_rows(project, entries):
    rows = [{'id':d['id'], 'identifier':identifier(d,project), 'title':d['title'],
             'path':safe_path(d['folder'],d['filename']), 'pages':entries[d['id']]['pages']}
            for d in project['documents']]
    order = index_settings.settings(project)['order']
    if order == 'identifier': rows.sort(key=lambda r:natural(r['identifier']))
    elif order == 'folder': rows.sort(key=lambda r:(natural(r['path'].rsplit('/',1)[0] if '/' in r['path'] else ''),natural(r['identifier'])))
    return rows


def paths_ok(paths):
    seen = set()
    for path in paths:
        normalized = unicodedata.normalize('NFC',path).casefold()
        if normalized in seen:
            raise ValueError('Путь перечня конфликтует с приложением. Измените имя или папку приложения: '+path)
        seen.add(normalized)
    for path in seen:
        parts = path.split('/')
        if any('/'.join(parts[:i]) in seen for i in range(1,len(parts))):
            raise ValueError('Имя файла перечня или приложения конфликтует с папкой. Измените путь приложения.')


class IndexPlans:
    def __init__(self, store):
        self.store = store
        self.lock = RLock()
        self.busy = set()
        self.plans = {}

    @contextmanager
    def operation(self, pid):
        with self.lock:
            if pid in self.busy: raise ValueError('Перечень уже рассчитывается или собирается. Дождитесь результата.')
            self.busy.add(pid)
        try: yield
        finally:
            with self.lock: self.busy.remove(pid)

    def checked(self, pid):
        p = self.store.load(pid)
        config = index_settings.settings(p)
        if not config['enabled']: raise ValueError('Сначала включите и сохраните перечень приложений.')
        if not p['documents']: raise ValueError('Добавьте PDF-приложения для перечня.')
        issues = self.store.validate(p)
        if issues: raise ValueError('Сборка заблокирована: '+issues[0]['message'])
        paths = [safe_path(d['folder'],d['filename']) for d in p['documents']]
        if p['main']: paths += ['Main document.docx','Main document.pdf']
        paths += ['Exhibit index.'+f for f in config['formats']]
        paths_ok(paths)
        return deepcopy(p)

    def current(self, pid, expected):
        p = self.checked(pid)
        if fingerprint(p) != expected: raise ValueError(STALE)
        return p

    def preview(self, pid):
        with self.operation(pid), io_errors():
            with LOCK: p = self.checked(pid)
            key, token = content_key(p), fingerprint(p)
            previous = self.plans.get(pid)
            if previous and previous['content_key'] == key:
                try:
                    intact = all(digest_bytes(Path(entry['file']).read_bytes()) == entry['sha256'] for entry in previous['entries'].values())
                except OSError:
                    intact = False
                if not intact:
                    self.plans.pop(pid)['temporary'].cleanup()
                    previous = None
            if previous and previous['content_key'] == key:
                with LOCK:
                    self.current(pid, token)
                    with self.lock:
                        result = {**previous,'token':token,'rows':ordered_rows(p,previous['entries'])}
                        self.plans[pid] = result
                return self.public(result, p)
            if previous:
                self.plans.pop(pid)['temporary'].cleanup()
                previous = None
            temporary = TemporaryDirectory(prefix='bl-submission-index-')
            try:
                entries, total = {}, 0
                for i,d in enumerate(p['documents']):
                    data = self.store.prepare(p,d)
                    total += len(data)
                    if total > MAX_BYTES or shutil.disk_usage(temporary.name).free < len(data)+16_000_000:
                        raise ValueError('Недостаточно временного места для перечня; предел подготовленных PDF — 8 ГБ.')
                    try: pages = len(PdfReader(BytesIO(data)).pages)
                    except (PdfReadError,ValueError) as exc:
                        raise ValueError('Не удалось проверить подготовленный PDF: '+d['filename']) from exc
                    if not pages: raise ValueError('В приложении нет проверяемых страниц.')
                    path = Path(temporary.name)/(str(i)+'.pdf');path.write_bytes(data)
                    entries[d['id']] = {'file':str(path),'pages':pages,'sha256':digest_bytes(data)}
                result = {'token':token,'content_key':key,'entries':entries,'temporary':temporary,
                          'rows':ordered_rows(p,entries),'bytes':total}
                with LOCK:
                    self.current(pid,token)
                    with self.lock:
                        for other in list(self.plans):
                            if other != pid and other not in self.busy:
                                self.plans.pop(other)['temporary'].cleanup()
                        if total+sum(v['bytes'] for k,v in self.plans.items() if k!=pid)>MAX_BYTES:
                            raise ValueError('Временное хранилище занято другой сборкой. Дождитесь её завершения.')
                        self.plans[pid] = result
                if previous: previous['temporary'].cleanup()
                return self.public(result,p)
            except BaseException:
                temporary.cleanup()
                raise

    def public(self, plan, p):
        return {'preview_token':plan['token'],'rows':deepcopy(plan['rows']),
                'count':len(plan['rows']),'settings':index_settings.settings(p)}

    def export(self, pid, token):
        from . import index_documents, history
        with self.operation(pid), io_errors():
            plan = self.plans.get(pid)
            if not plan or not token or token != plan['token']: raise ValueError(STALE)
            with LOCK: p = self.current(pid,token)
            config = index_settings.settings(p)
            out = BytesIO()
            with ZipFile(out,'w',ZIP_DEFLATED) as archive:
                for row in plan['rows']:
                    entry = plan['entries'][row['id']]
                    data = Path(entry['file']).read_bytes()
                    if digest_bytes(data) != entry['sha256'] or len(PdfReader(BytesIO(data)).pages) != row['pages']:
                        raise ValueError('Подготовленный PDF изменён. Обновите перечень.')
                    archive.writestr('Submission/'+row['path'],data)
                if p['main']:
                    archive.writestr('Submission/Main document.docx',self.store.linked_main(p)[0])
                    archive.writestr('Submission/Main document.pdf',self.store.prepare_main_pdf(p))
                for fmt in config['formats']:
                    archive.writestr('Submission/Exhibit index.'+fmt,index_documents.generate(fmt,config,plan['rows']))
            data = out.getvalue()
            index_documents.verify_package(data,config,plan['rows'])
            with LOCK:
                self.current(pid,token)
                history.save_export(self.store,p,data)
            return data

    def close(self):
        for plan in self.plans.values(): plan['temporary'].cleanup()
        self.plans.clear()


def digest_bytes(data):
    from hashlib import sha256
    return sha256(data).hexdigest()


def manager(store):
    with LOCK:
        if not hasattr(store,'index_plans'): store.index_plans = IndexPlans(store)
        return store.index_plans
