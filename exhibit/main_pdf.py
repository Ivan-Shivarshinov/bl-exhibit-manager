"""Local office conversion; unique markers prove every managed link survived export."""
from collections import Counter
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from urllib.parse import unquote
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED
import os
import shutil
import subprocess
import sys

from lxml import etree as E
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DictionaryObject, NameObject, NumberObject, TextStringObject
from . import word
from .external_process import popen

CONVERSION_LOCK = Lock()
TIMEOUT = 120


def available():
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"):
                return {"available": True, "engine": "word", "label": "Microsoft Word"}
        except OSError:
            pass
    candidates = [shutil.which("soffice"), "/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
        if base: candidates.append(str(Path(base) / "LibreOffice/program/soffice.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return {"available": True, "engine": "libreoffice", "label": "LibreOffice", "executable": candidate}
    return {"available": False, "engine": None, "label": "Конвертер не найден", "message": "Для основного PDF установите Microsoft Word на Windows либо LibreOffice на Windows/macOS. Затем повторите подготовку PDF. Исходники сохранены."}


def marked_docx(data, paths):
    parts = word.package(data)
    markers = {}
    if word.FOOT not in parts or word.RELS not in parts: return data, markers
    root, rels = word.xml(parts[word.FOOT]), word.xml(parts[word.RELS])
    by_id = {r.get("Id"): r for r in rels}
    known = set(paths)
    for link in root.findall(".//w:hyperlink", word.NS):
        rel = by_id.get(link.get(f"{{{word.R}}}id"))
        if rel is None or rel.get("TargetMode") != "External": continue
        path = unquote(rel.get("Target", ""))
        if path not in known: continue
        token = uuid4().hex
        marker = "https://exhibit.invalid/" + token
        rid = "rIdPdf" + token
        E.SubElement(rels, f"{{{word.REL}}}Relationship", Id=rid, Type=word.R+"/hyperlink", Target=marker, TargetMode="External")
        link.set(f"{{{word.R}}}id", rid)
        markers[marker] = path
    parts[word.FOOT] = E.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    parts[word.RELS] = E.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        for name, content in parts.items(): z.writestr(name, content)
    return out.getvalue(), markers


def portable_links(data, markers):
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted or not reader.pages: raise ValueError("Конвертер вернул пустой или защищённый PDF.")
        writer = PdfWriter(clone_from=reader)
        seen = Counter()
        for page in writer.pages:
            for item in page.get("/Annots", []):
                annotation = item.get_object()
                action = annotation.get("/A")
                if not action: continue
                action = action.get_object()
                uri = str(action.get("/URI", ""))
                if uri not in markers: continue
                path = markers[uri]
                annotation[NameObject("/A")] = DictionaryObject({
                    NameObject("/S"): NameObject("/GoToR"),
                    NameObject("/F"): DictionaryObject({NameObject("/Type"): NameObject("/Filespec"), NameObject("/F"): TextStringObject(path), NameObject("/UF"): TextStringObject(path)}),
                    NameObject("/D"): ArrayObject([NumberObject(0), NameObject("/Fit")]),
                    NameObject("/NewWindow"): BooleanObject(True),
                })
                seen[uri] += 1
        missing = set(markers) - seen.keys()
        if missing: raise ValueError(f"При конвертации потеряны ссылки на приложения: {len(missing)}. PDF и ZIP не выданы. Проверьте сноски в исходном Word.")
        out = BytesIO(); writer.write(out)
        return out.getvalue()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Не удалось проверить основной PDF, созданный конвертером.") from exc


def office_export(source, target, directory, engine):
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    if engine["engine"] == "word":
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_name("word_pdf.ps1")), "-InputDocument", str(source), "-OutputPdf", str(target), "-PidFile", str(directory / "word.pid")]
    else:
        profile = directory / "office-profile"
        profile.mkdir()
        # A private profile avoids using or modifying the user's open LibreOffice session.
        (profile / "user").mkdir()
        (profile / "user/registrymodifications.xcu").write_text('''<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item><item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item></oor:items>''', encoding="utf-8")
        command = [engine["executable"], "-env:UserInstallation="+profile.as_uri(), "--headless", "--nologo", "--norestore", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(target.parent), str(source)]
    proc = popen(command, cwd=directory, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
    try:
        result = proc.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        # Kill only this conversion process tree, never arbitrary Word processes.
        if sys.platform == "win32":
            subprocess.run(["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            pidfile = directory / "word.pid"
            if pidfile.is_file():
                owned_pid = pidfile.read_text().strip()
                if owned_pid.isdigit():
                    subprocess.run(["taskkill.exe", "/PID", owned_pid, "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise ValueError("Конвертация PDF превысила 120 секунд. Закройте служебное окно Word, если оно осталось, и повторите. ZIP не создан.") from None
    if result or not target.is_file():
        raise ValueError("Не удалось создать основной PDF. Проверьте, что локальный конвертер запускается и документ открывается без диалогов, затем повторите. ZIP не создан.")


def convert_docx(data, paths):
    engine = available()
    if not engine["available"]: raise ValueError(engine["message"])
    marked, markers = marked_docx(data, paths)
    with CONVERSION_LOCK, TemporaryDirectory(prefix="bl-exhibit-pdf-") as temp:
        directory = Path(temp)
        source = directory / "Main document.docx"
        target = directory / "Main document.pdf"
        source.write_bytes(marked)
        try: office_export(source, target, directory, engine)
        except OSError as exc: raise ValueError("Не удалось запустить локальный конвертер PDF. Проверьте его установку.") from exc
        result = portable_links(target.read_bytes(), markers)
    return result
