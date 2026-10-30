"""Loopback UI. Translation uses the user's official CLI; credentials stay with that CLI."""
from pathlib import Path
from threading import RLock
import logging
from urllib.parse import urlparse
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse, Response, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .project import Store, LOCK
from . import pdf
from .translation import Translations
from .translation_cli import status as translation_status
from . import __version__
from . import backup
from starlette.concurrency import run_in_threadpool
import shutil
import re
from .local_access import LocalAccess, audience, DENIED

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
from .word_connection import WordPanels
app.state.word_panels = WordPanels()
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
store = Store()
translations = Translations(store)
ACCESS_LOCK = RLock()


def get_access():
    with ACCESS_LOCK:
        access = getattr(app.state, 'local_access', None)
        if access is None or access.private.folder.parent != store.root:
            access = app.state.local_access = LocalAccess(store.root)
        return access


def word_origin(request):
    configured = getattr(app.state, 'word_connection', {})
    origin = urlparse(configured.get('origin', ''))
    return request.scope['scheme'] == 'https' and request.scope['server'][1] == origin.port and configured.get('configured') is True


def word_scope(request):
    path, method = request.url.path, request.method
    return (method == 'GET' and (path == '/api/projects' or re.fullmatch(r'/api/word/projects/[a-f0-9]{32}', path))
            or method == 'POST' and path in ('/api/word/connection', '/api/word/rewrite')
            or method == 'POST' and re.fullmatch(r'/api/projects/[a-f0-9]{32}/upload', path)
            and request.query_params.getlist('kind') == ['main'])


async def access_body(request, keys):
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > 4096: raise ValueError('Слишком большой запрос доступа.')
    import json
    try: value = json.loads(data)
    except (ValueError, UnicodeError) as exc: raise ValueError('Некорректный запрос доступа.') from exc
    if not isinstance(value, dict) or set(value) != set(keys): raise ValueError('Некорректный запрос доступа.')
    return value


@app.get("/api/health")
def health(request: Request, challenge: str | None = None):
    value = {"application": "bl-exhibit-manager", "status": "ok", "version": __version__}
    if challenge is not None:
        if not re.fullmatch('[a-f0-9]{64}', challenge): raise ValueError('Некорректная проверка экземпляра.')
        access = get_access()
        value.update(instance=access.instance, proof=access.proof(challenge, request.scope['server'][1]))
    return value


@app.post('/api/access/session')
async def owner_session(request: Request):
    data = await access_body(request, ('bootstrap',))
    access, aud = get_access(), audience(request)
    try: token = access.owner_session(data['bootstrap'], aud)
    except ValueError: return JSONResponse({'detail': DENIED}, status_code=401)
    return {'authorized': True, 'token': token}


@app.get('/api/access/status')
def access_status():
    return {'authorized': True}


@app.post('/api/access/word/request')
async def request_word_access(request: Request):
    await access_body(request, ())
    if not word_origin(request): return JSONResponse({'detail': DENIED}, status_code=401)
    return get_access().request_word(audience(request))


@app.post('/api/access/word/poll')
async def poll_word_access(request: Request):
    data = await access_body(request, ('id', 'secret'))
    if not word_origin(request): return JSONResponse({'detail': DENIED}, status_code=401)
    try: return get_access().poll_word(data['id'], data['secret'], audience(request))
    except ValueError: return JSONResponse({'detail': DENIED}, status_code=401)


@app.post('/api/access/word/session')
async def word_access_session(request: Request):
    data = await access_body(request, ('grant',))
    if not word_origin(request): return JSONResponse({'detail': DENIED}, status_code=401)
    try: return {'token': get_access().word_session(data['grant'], audience(request))}
    except ValueError: return JSONResponse({'detail': DENIED}, status_code=401)


@app.get('/api/access/word/requests')
def word_access_requests():
    return get_access().requests()


def revoke_word_access():
    get_access().revoke_word()
    app.state.word_panels.clear()


@app.post('/api/access/word/revoke')
async def revoke_word_permissions(request: Request):
    data = await access_body(request, ('consent',))
    if data['consent'] is not True: raise ValueError('Подтвердите отзыв разрешений панелей Word.')
    revoke_word_access()
    return {'revoked': True}


@app.post('/api/access/word/{pid}/approve')
async def approve_word_access(pid: str, request: Request):
    data = await access_body(request, ('consent',))
    if data['consent'] is not True: raise ValueError('Подтвердите совпадение кода в панели Word.')
    get_access().approve_word(pid)
    return {'approved': True}


@app.post("/api/application/stop")
def stop_application(background: BackgroundTasks):
    stop = getattr(app.state, "stop_server", None)
    if stop is None:
        raise ValueError("Этот сервер запущен вручную. Остановите его в исходном терминале через Ctrl+C.")
    with LOCK:
        if translations.active:
            raise ValueError("Сначала дождитесь перевода или остановите его в окне переводчика. Готовые части сохранены.")
    if getattr(app.state,'material_store',None) is store:
        with app.state.material_jobs.lock:
            if app.state.material_jobs.active:
                raise ValueError('Дождитесь операции библиотеки или отмените её перед остановкой приложения.')
    background.add_task(stop)
    return {"stopping": True}


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.headers.get('host', '').split(':')[0] not in ('localhost', '127.0.0.1'):
        return JSONResponse({'detail': 'Недопустимый локальный адрес.'}, status_code=400)
    if any(k in request.headers for k in ('forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'x-forwarded-port')):
        return JSONResponse({'detail': 'Проксирование локального запроса не поддерживается.'}, status_code=403)
    if request.scope['scheme'] == 'http' and not hasattr(app.state, 'http_port'):
        app.state.http_port = request.scope['server'][1]
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "Запрос из другого источника запрещён."}, status_code=403)
    if request.method not in ("GET", "HEAD") and request.headers.get("X-Exhibit-Local") != "1":
        return JSONResponse({"detail": "Требуется локальный запрос интерфейса."}, status_code=403)
    path = request.url.path
    if request.scope['scheme'] == 'https' and request.method == 'GET' and (
            path == '/' or re.fullmatch(r'/api/word/open/[a-f0-9]{32}/[a-f0-9]{32}/[a-f0-9]{32}', path)):
        from urllib.parse import urlencode
        query = '?' + urlencode({'open': path}) if path != '/' else ''
        return RedirectResponse(f"http://127.0.0.1:{getattr(app.state, 'http_port', 8765)}/{query}", status_code=307)
    public = path == '/api/health' and request.method in ('GET', 'HEAD') or request.method == 'POST' and path in (
        '/api/access/session', '/api/access/word/request', '/api/access/word/poll', '/api/access/word/session')
    if (path == '/api' or path.startswith('/api/')) and not public:
        access, aud = get_access(), audience(request)
        bearer = request.headers.get('authorization', '')
        role = access.role(bearer[7:] if bearer.startswith('Bearer ') else None, aud)
        if role is None or role == 'word' and (not word_origin(request) or not word_scope(request)):
            if request.method == 'GET' and path.startswith('/api/word/open/'):
                if re.fullmatch(r'/api/word/open/[a-f0-9]{32}/[a-f0-9]{32}/[a-f0-9]{32}', path):
                    from urllib.parse import urlencode
                    return RedirectResponse('/?' + urlencode({'open': path}), status_code=307)
            return JSONResponse({'detail': DENIED}, status_code=401, headers={'Cache-Control': 'no-store'})
    try:
        response = await call_next(request)
    except Exception:
        logging.getLogger(__name__).exception("Request failed: %s %s", request.method, request.url.path)
        raise
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; object-src 'none'; frame-ancestors 'none'; connect-src 'self'"
    if request.url.path.startswith('/word/'):
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://appsforoffice.microsoft.com; style-src 'self'; img-src 'self' data:; connect-src 'self' https://appsforoffice.microsoft.com; object-src 'none'; frame-ancestors 'self' https://*.office.com https://*.officeapps.live.com https://*.microsoft365.com"
    return response


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/projects")
def projects():
    with LOCK:
        return store.list()


@app.post("/api/projects")
async def create(request: Request):
    body = await request.json()
    with LOCK:
        return store.public(store.create(body.get("name")))


@app.post("/api/demo")
def demo():
    from .samples import demo_project
    with LOCK:
        return store.public(demo_project(store))


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    with LOCK:
        return store.public(store.load(pid))


@app.post('/api/projects/{pid}/backup')
def save_project_backup(pid: str):
    with LOCK:
        folder = backup.work_folder(store)
        try:
            backup.save_archive(store, pid, folder/'download.zip', translations.active)
        except Exception:
            shutil.rmtree(folder)
            raise
    return {'token': folder.name}


@app.get('/api/backups/{token}/download')
def download_project_backup(token: str, background: BackgroundTasks):
    folder = backup.work_folder(store, token)
    path = folder/'download.zip'
    if not path.is_file(): raise ValueError('Архив для скачивания не найден.')
    background.add_task(shutil.rmtree, folder, ignore_errors=True)
    return FileResponse(path, media_type='application/zip', filename='Exhibit-project.zip', background=background)


@app.post('/api/backups/preview')
async def preview_project_backup(request: Request):
    folder = backup.work_folder(store)
    try:
        with backup.io_errors(), (folder/'restore.zip').open('xb') as stream:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > backup.MAX_BYTES: raise ValueError('Архив превышает предел 8 ГБ.')
                stream.write(chunk)
        result = await run_in_threadpool(backup.preview, store, folder/'restore.zip')
        return {**result, 'token': folder.name}
    except BaseException:
        shutil.rmtree(folder)
        raise


@app.post('/api/backups/{token}/restore')
async def restore_project_backup(token: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict) or type(body.get('copy', False)) is not bool: raise ValueError('Выберите способ восстановления.')
    folder = backup.work_folder(store, token)
    p = await run_in_threadpool(backup.restore_archive, store, folder/'restore.zip', body.get('copy', False))
    shutil.rmtree(folder)
    with LOCK: return store.public(store.load(p['id']))


@app.post('/api/backups/{token}/cancel')
def cancel_project_backup(token: str):
    with LOCK: shutil.rmtree(backup.work_folder(store, token))
    return {'cancelled': True}


@app.get('/api/projects/{pid}/exports')
def exports(pid: str):
    from .history import list_exports
    with LOCK: return list_exports(store, store.load(pid))


@app.get('/api/projects/{pid}/exports/{eid}')
def saved_export(pid: str, eid: str):
    from .history import read_export
    with LOCK: data = read_export(store, store.load(pid), eid)
    return Response(data, media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="Submission.zip"'})


@app.get('/api/word/projects/{pid}')
def word_catalog(pid: str):
    from .word_bridge import catalog
    with LOCK: return catalog(store.load(pid))


@app.get('/api/word/status')
def word_status():
    return getattr(app.state, 'word_connection', {'configured': False})


@app.post('/api/word/connection')
async def word_panel_connection(request: Request):
    connection = getattr(app.state, 'word_connection', {})
    if request.url.scheme != 'https' or not connection.get('configured') or str(request.base_url).rstrip('/') != connection.get('origin'):
        return JSONResponse({'detail': 'Подтверждение панели доступно только через её локальное HTTPS-соединение.'}, status_code=403)
    return app.state.word_panels.update(await request.json())


@app.post('/api/word/rewrite')
async def word_rewrite(request: Request):
    from .word_bridge import rewrite_citation_ooxml
    data = await request.json()
    if not isinstance(data, dict): raise ValueError('Некорректный запрос Word.')
    return {'ooxml': rewrite_citation_ooxml(data.get('ooxml'), data.get('expected'), data.get('replacement'), data.get('address'), data.get('identifier', ''))}


@app.get('/api/word/manifest')
def word_manifest():
    path = store.root / 'BLExhibitManager.Word.xml'
    if not path.exists(): raise ValueError('Сначала настройте подключение Word по инструкции.')
    return FileResponse(path, media_type='application/xml', filename=path.name)


@app.get('/api/word/instructions')
def word_instructions():
    path = Path(__file__).resolve().parent / 'assets' / 'WORD-SETUP.txt'
    return Response(path.read_text('utf-8'), media_type='text/plain; charset=utf-8')


@app.post('/api/projects/{pid}/citation-style')
async def citation_style(pid: str, request: Request):
    from .word_bridge import check_settings
    values = check_settings(await request.json())
    with LOCK:
        p = store.load(pid); p['citation_style'] = values; store.save(p)
        return store.public(p)


@app.get('/api/word/open/{pid}/{did}/{cid}')
def word_open(pid: str, did: str, cid: str):
    with LOCK:
        p = store.load(pid); d = store.document(p, did)
        data = store.prepare(p, d)
    return Response(data, media_type='application/pdf')


@app.post("/api/projects/{pid}/upload")
async def upload(pid: str, request: Request, name: str, kind: str = "original", did: str | None = None, mode: str = "prepare"):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 50_000_000:
            raise ValueError("Максимальный размер файла — 50 МБ.")
    with LOCK:
        project = store.load(pid)
        expected = request.headers.get('X-Exhibit-Main-Sha')
        if kind == 'main' and expected is not None and expected != (project.get('main') or {}).get('sha256', ''):
            raise ValueError('Основной DOCX в подаче изменился. Обновите список в панели Word и проверьте выбранную подачу перед повторной передачей.')
        return store.public(store.upload(project, name, bytes(data), kind, did, mode))


@app.post("/api/projects/{pid}/ready-pdfs")
async def ready_pdfs(pid: str, request: Request):
    body = await request.json()
    with LOCK:
        return store.public(store.use_originals(store.load(pid), body.get("document_ids")))


@app.post("/api/projects/{pid}/documents/{did}")
async def update(pid: str, did: str, request: Request):
    body = await request.json()
    with LOCK:
        return store.public(store.update(store.load(pid), did, body))


@app.post("/api/projects/{pid}/style")
async def style(pid: str, request: Request):
    body = await request.json()
    with LOCK:
        return store.public(store.style(store.load(pid), body))


@app.post("/api/projects/{pid}/batch/preview")
async def batch_preview(pid: str, request: Request):
    body = await request.json()
    with LOCK:
        return store.batch_plan(store.load(pid), body)[1]


@app.post("/api/projects/{pid}/batch/apply")
async def batch_apply(pid: str, request: Request):
    body = await request.json()
    with LOCK:
        return store.public(store.batch_apply(store.load(pid), body.get("request"), body.get("token")))


@app.post("/api/projects/{pid}/documents/{did}/review/{action}")
def approve(pid: str, did: str, action: str):
    with LOCK:
        return store.public(store.approve(store.load(pid), did, action))


@app.post("/api/projects/{pid}/scan")
def scan(pid: str):
    with LOCK:
        return store.public(store.scan(store.load(pid)))


@app.post("/api/projects/{pid}/references")
async def map_reference(pid: str, request: Request):
    b = await request.json()
    with LOCK:
        return store.public(store.map_reference(store.load(pid), b["key"], b["target"], b.get("all_same", False), b.get("keep_original", False)))


@app.post("/api/projects/{pid}/references/add")
async def add_reference(pid: str, request: Request):
    b = await request.json()
    with LOCK:
        return store.public(store.add_reference(store.load(pid), b["fid"], b["paragraph"], b["mention"], b.get('start'), b.get('end'), b.get('target')))


@app.post("/api/projects/{pid}/references/exclude")
async def exclude_reference(pid: str, request: Request):
    b = await request.json()
    if type(b.get('restore',False)) is not bool: raise ValueError('Некорректное действие.')
    with LOCK:
        return store.public(store.exclude_reference(store.load(pid), b['key'], b.get('restore',False)))


@app.post("/api/projects/{pid}/references/confirm")
def confirm_links(pid: str):
    with LOCK:
        return store.public(store.confirm_links(store.load(pid)))


@app.post("/api/projects/{pid}/references/range")
async def reference_range(pid: str,request: Request):
    b=await request.json()
    with LOCK:return store.public(store.reference_range(store.load(pid),b['key'],b.get('start'),b.get('end')))


@app.get("/api/projects/{pid}/documents/{did}/preview")
def preview(pid: str, did: str, part: str = "original", page: int = 1):
    with LOCK:
        p = store.load(pid)
        d = store.document(p, did)
        if part == "result":
            data = store.prepare(p, d)
        elif part in ("original", "translation"):
            data = store.source(p, d[part])
        else:
            raise ValueError("Неизвестная часть.")
        return Response(pdf.render_png(data, page), media_type="image/png")


@app.get("/api/projects/{pid}/documents/{did}/pdf")
def result_pdf(pid: str, did: str):
    with LOCK:
        p = store.load(pid)
        data = store.prepare(p, store.document(p, did))
        return Response(data, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="Preview.pdf"'})


@app.post("/api/projects/{pid}/export")
async def export(pid: str, request: Request):
    raw = await request.body()
    body = await request.json() if raw else {}
    if not isinstance(body,dict): raise ValueError('Некорректные параметры сборки.')
    with LOCK:
        p = store.load(pid)
    def build():
        from .index_settings import settings
        if settings(p)['enabled']: return store.export(p,body.get('preview_token'))
        # Preserve serialization of the existing export path when the index is off.
        with LOCK: return store.export(store.load(pid),body.get('preview_token'))
    data = await run_in_threadpool(build)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="Submission.zip"'})


@app.get('/api/projects/{pid}/submission-index')
def get_index_settings(pid: str):
    from .index_settings import settings
    with LOCK: return settings(store.load(pid))


@app.patch('/api/projects/{pid}/submission-index')
async def save_index_settings(pid: str, request: Request):
    from .index_settings import save
    body = await request.json()
    with LOCK: return store.public(save(store,store.load(pid),body))


@app.get('/api/projects/{pid}/submission-index/status')
def index_status(pid: str):
    from .submission_index import fingerprint
    with LOCK: return {'fingerprint':fingerprint(store.load(pid))}


@app.post('/api/projects/{pid}/submission-index/preview')
async def preview_index(pid: str):
    from .submission_index import manager
    return await run_in_threadpool(manager(store).preview,pid)


@app.get("/api/projects/{pid}/main-pdf/status")
def main_pdf_status(pid: str):
    with LOCK: return store.main_pdf_status(store.load(pid))


@app.post("/api/projects/{pid}/main-pdf/prepare")
def prepare_main_pdf(pid: str):
    with LOCK:
        p = store.load(pid)
    # Conversion does not hold the global project lock: health and other projects remain usable.
    store.prepare_main_pdf(p, refresh=True)
    with LOCK: return store.main_pdf_status(store.load(pid))


@app.get("/api/projects/{pid}/main-pdf/preview")
def preview_main_pdf(pid: str, page: int = 1):
    with LOCK: data = store.read_main_pdf(store.load(pid))
    return Response(pdf.render_png(data, page), media_type="image/png")


@app.get("/api/projects/{pid}/main-pdf/download")
def download_main_pdf(pid: str):
    with LOCK: data = store.read_main_pdf(store.load(pid))
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="Main document.pdf"'})


@app.get("/api/demo-missing")
def demo_missing():
    from .samples import make_pdf
    data = make_pdf("Дополнительный учебный материал RLA-99", [["Вымышленный документ для устранения ненайденной ссылки."]])
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="RLA-99.pdf"'})


@app.get("/api/translation/providers/{provider}")
def provider_status(provider: str):
    return translation_status(provider)


@app.get("/api/projects/{pid}/documents/{did}/translation/source")
def translation_source(pid: str, did: str):
    return translations.source(pid, did)


@app.get("/api/projects/{pid}/documents/{did}/translation")
def translation_state(pid: str, did: str):
    return translations.state(pid, did)


@app.post("/api/projects/{pid}/documents/{did}/translation/{action}")
async def translation_action(pid: str, did: str, action: str, request: Request):
    body = await request.json()
    if action == "source-preview": return translations.preview_source(pid, did, body.get("options"))
    if action == "start": return translations.start(pid, did, body)
    if action == "cancel": return translations.cancel(pid, did)
    if action == "save": return translations.save(pid, did, body)
    if action == "apply": return translations.apply(pid, did, body)
    raise ValueError("Неизвестное действие перевода.")


@app.get("/api/projects/{pid}/documents/{did}/translation/pdf")
def translation_pdf(pid: str, did: str, revision: int):
    return Response(translations.preview(pid, did, revision), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="Translation.pdf"'})


@app.get("/api/projects/{pid}/documents/{did}/translation/preview")
def translation_preview(pid: str, did: str, revision: int, page: int = 1):
    data = translations.preview(pid, did, revision)
    return Response(pdf.render_png(data, page), media_type="image/png", headers={"X-Page-Count": str(pdf.inspect_pdf(data)["pages"])})


@app.get('/api/setup')
def setup_summary():
    from . import diagnostics, word_setup
    components = {'converter': diagnostics.converter(), 'word': word_setup.status(store.root, app),
                  'claude': {'state': 'unchecked'}, 'codex': {'state': 'unchecked'}}
    return {'components': components, 'report': diagnostics.report(components)}


@app.post('/api/setup/check/{component}')
def setup_check(component: str):
    from . import diagnostics, word_setup
    if component == 'converter': return diagnostics.converter()
    if component == 'word': return word_setup.status(store.root, app, verify=True)
    return diagnostics.provider(component)


@app.post('/api/setup/report')
async def setup_report(request: Request):
    from .diagnostics import report
    body = await request.json()
    if not isinstance(body, dict) or any(not isinstance(v, dict) for v in body.values()):
        raise ValueError('Некорректная сводка.')
    return report(body)


@app.post('/api/setup/word')
async def setup_word(request: Request):
    from . import word_setup, word_tls
    body = await request.json()
    if not isinstance(body, dict): raise ValueError('Укажите HTTPS-порт.')
    def prepare():
        with word_setup.SETUP_LOCK:
            http_port = getattr(app.state, 'http_port', 8765)
            word_setup.prepare(store.root, http_port, body.get('port'))
            listener = getattr(app.state, 'word_listener', None)
            if not listener or not listener[1].is_alive():
                word_tls.start_optional(app, store.root, http_port)
            return word_setup.status(store.root, app)
    return await run_in_threadpool(prepare)


@app.post('/api/setup/word/install')
async def install_word(request: Request):
    from .word_install import manager
    from .word_setup import status
    body = await request.json()
    if (not isinstance(body, dict) or set(body) - {'replacement_token'} != {'consent'} or body['consent'] is not True
            or ('replacement_token' in body and (not isinstance(body['replacement_token'], str) or len(body['replacement_token']) != 64))):
        raise ValueError('Подтвердите настройку локального сертификата, регистрацию панели и открытие учебного Word.')
    manager(app, store.root).begin(replacement_token=body.get('replacement_token'))
    return await run_in_threadpool(status, store.root, app)


@app.post('/api/setup/word/cancel')
def cancel_word():
    from .word_install import manager
    from .word_setup import status
    manager(app, store.root).stop()
    return status(store.root, app)


@app.post('/api/setup/word/remove')
async def remove_word(request: Request):
    from .word_install import manager
    from .word_setup import status
    body = await request.json()
    if not isinstance(body, dict) or set(body) != {'consent'} or body['consent'] is not True: raise ValueError('Подтвердите удаление собственного подключения панели.')
    manager(app, store.root).begin(remove=True)
    revoke_word_access()
    return await run_in_threadpool(status, store.root, app)


@app.post('/api/setup/word/open')
async def open_word():
    from .word_install import manager
    from .word_setup import status
    await run_in_threadpool(manager(app, store.root).open)
    return status(store.root, app)


@app.get('/api/setup/word/certificate')
def setup_certificate():
    from .word_setup import configuration
    config, _ = configuration(store.root, getattr(app.state, 'http_port', 8765))
    if config.get('generated') is not True:
        raise ValueError('Для прежнего сертификата используйте инструкцию его поставщика.')
    return FileResponse(config['cert'], media_type='application/x-x509-ca-cert', filename='BL-Exhibit-localhost.crt')


MATERIAL_SERVICES_LOCK=RLock()


def material_services():
    from .library import Library
    from .material_jobs import Jobs
    with MATERIAL_SERVICES_LOCK:
        if getattr(app.state,'material_store',None) is not store:
            app.state.material_library=Library(store)
            app.state.material_jobs=Jobs(app.state.material_library)
            app.state.material_store=store
        return app.state.material_library, app.state.material_jobs


@app.get('/api/libraries')
def list_libraries():
    return material_services()[0].list()


@app.post('/api/libraries')
async def create_library(request: Request):
    body=await request.json()
    return await run_in_threadpool(material_services()[0].create,body.get('name'))


@app.get('/api/libraries/{lid}/materials')
def library_catalog(lid: str, request: Request, q: str='', offset: int=0, limit: int=50, sort: str='title'):
    return material_services()[0].catalog(lid,q,offset,limit,sort,**{k:request.query_params.get(k) for k in ('category','language','document_date','tag','translation','hidden')})


@app.get('/api/libraries/{lid}/materials/{mid}/versions/{vid}')
def material_version(lid: str, mid: str, vid: str):
    return material_services()[0].version(lid,mid,vid)


@app.get('/api/libraries/{lid}/materials/{mid}')
def material_card(lid: str, mid: str):
    from .library import uid
    card=material_services()[0].load(lid)['materials'].get(uid(mid))
    if not card: raise ValueError('Материал не найден.')
    return card


@app.get('/api/material-previews/{token}/{vid}')
def planned_material_page(token: str, vid: str, role: str='original', page: int=1):
    return Response(material_services()[0].planned_page(token,vid,role,page),media_type='image/png')


@app.get('/api/projects/{pid}/library-status')
def library_status(pid: str):
    return material_services()[0].notifications(pid)


@app.get('/api/libraries/{lid}/materials/{mid}/versions/{vid}/preview')
def material_page(lid: str, mid: str, vid: str, role: str='original', page: int=1, scale: float=1.2):
    return Response(material_services()[0].preview_page(lid,mid,vid,role,page,scale),media_type='image/png')


@app.post('/api/material-operations')
async def material_operation(request: Request):
    body=await request.json()
    return material_services()[1].start(body.get('action'),body.get('arguments'))


@app.get('/api/material-operations/{token}')
def material_operation_status(token: str):
    return material_services()[1].status(token)


@app.post('/api/material-operations/{token}/cancel')
def material_operation_cancel(token: str):
    return material_services()[1].cancel(token)


@app.post('/api/library-files')
async def library_file_upload(request: Request, name: str, archive: bool=False):
    lib,_=material_services();folder=lib.pending();size=0
    limit=backup.MAX_BYTES if archive else backup.MAX_INPUT
    path=folder/('library.zip' if archive else 'upload.pdf')
    try:
        with path.open('xb') as stream:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>limit: raise ValueError('Файл превышает допустимый размер: 8 ГБ для архива, 50 МБ для PDF.')
                stream.write(chunk)
        if not size: raise ValueError('Выберите непустой файл.')
        if archive: return {'token':folder.name}
        if not name.lower().endswith('.pdf'): raise ValueError('Выберите PDF.')
        # Receipt is temporary and role-free; publication checks each chosen role.
        part=await run_in_threadpool(lib.stage_part,folder,name,path.read_bytes(),allow_forms=True)
        from .library import atomic_json
        atomic_json(folder/'part.json',part)
        path.unlink()
        return {'token':folder.name,'part':part}
    except Exception:
        shutil.rmtree(folder,ignore_errors=True);raise


@app.get('/api/library-archives/{token}/download')
def library_archive_download(token: str):
    folder=material_services()[0].pending(token);path=folder/'library.zip'
    if not path.is_file(): raise ValueError('Архив библиотеки ещё не готов.')
    return FileResponse(path,media_type='application/zip',filename='Exhibit-library.zip')


@app.get('/api/library-files/{token}/preview')
def uploaded_library_page(token: str, page: int=1):
    lib,_=material_services();folder=lib.pending(token)
    from .backup import json_data
    part=json_data((folder/'part.json').read_bytes())
    with (folder/part['blob']).open('rb') as f:
        if backup.hash_stream(f)!={k:part[k] for k in ('size','sha256')}: raise ValueError('Загруженный PDF повреждён.')
    return Response(pdf.render_png((folder/part['blob']).read_bytes(),page,1.2),media_type='image/png')


@app.post('/api/libraries/{lid}/materials/{mid}/hidden')
async def material_hidden(lid: str, mid: str, request: Request):
    body=await request.json()
    return await run_in_threadpool(material_services()[0].hide,lid,mid,body.get('hidden'),body.get('revision'))


@app.get('/api/libraries/{lid}/materials/{mid}/usage')
def material_usage(lid: str, mid: str):
    return material_services()[0].usage(lid,mid)


DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
else:
    @app.get("/")
    def build_needed():
        return JSONResponse({"message": "Сначала соберите интерфейс: cd web; npm ci; npm run build"}, status_code=503)
