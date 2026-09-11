"""Loopback UI. Translation uses the user's official CLI; credentials stay with that CLI."""
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .project import Store, LOCK
from . import pdf
from .translation import Translations
from .translation_cli import status as translation_status

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
store = Store()
translations = Translations(store)


@app.get("/api/health")
def health():
    return {"application": "bl-exhibit-manager", "status": "ok"}


@app.middleware("http")
async def local_only(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "Запрос из другого источника запрещён."}, status_code=403)
    if request.method not in ("GET", "HEAD") and request.headers.get("X-Exhibit-Local") != "1":
        return JSONResponse({"detail": "Требуется локальный запрос интерфейса."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; object-src 'none'; frame-ancestors 'none'; connect-src 'self'"
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


@app.post("/api/projects/{pid}/upload")
async def upload(pid: str, request: Request, name: str, kind: str = "original", did: str | None = None):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 50_000_000:
            raise ValueError("Максимальный размер файла — 50 МБ.")
    with LOCK:
        return store.public(store.upload(store.load(pid), name, bytes(data), kind, did))


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
        return store.public(store.map_reference(store.load(pid), b["key"], b["target"], b.get("all_same", False)))


@app.post("/api/projects/{pid}/references/add")
async def add_reference(pid: str, request: Request):
    b = await request.json()
    with LOCK:
        return store.public(store.add_reference(store.load(pid), b["fid"], b["paragraph"], b["mention"]))


@app.post("/api/projects/{pid}/references/confirm")
def confirm_links(pid: str):
    with LOCK:
        return store.public(store.confirm_links(store.load(pid)))


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
def export(pid: str):
    with LOCK:
        p = store.load(pid)
    return Response(store.export(p), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="Submission.zip"'})


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


DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
else:
    @app.get("/")
    def build_needed():
        return JSONResponse({"message": "Сначала соберите интерфейс: cd web; npm ci; npm run build"}, status_code=503)
