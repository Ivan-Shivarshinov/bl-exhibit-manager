"""Recoverable translation drafts, deliberately separate from approved PDF attachments."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import re
from threading import Event, Thread
from uuid import uuid4
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

from . import pdf, translation_cli
from .project import LOCK, digest
from . import translation_source

LANGUAGES = {"English", "Русский", "Français", "Deutsch", "Español"}
MAX_TEXT = 120_000


def source_text(data):
    parts = []
    total = 0
    for page_no, page in enumerate(PdfReader(BytesIO(data)).pages, 1):
        text = (page.extract_text() or "").strip()
        if not text or "\ufffd" in text or not any(c.isalpha() for c in text):
            raise ValueError(f"На странице {page_no} нет надёжно извлекаемого текста. OCR сканов пока недоступен; прикрепите готовый перевод PDF.")
        total += len(text)
        if total > MAX_TEXT:
            raise ValueError("Для одного перевода доступно до 120 000 знаков. Разделите исходный PDF или прикрепите готовый перевод.")
        # Bounded pieces keep outputs below model response limits, without dropping characters.
        while text:
            split = len(text) if len(text) <= 6000 else max(text.rfind("\n", 0, 6000), text.rfind(" ", 0, 6000))
            if split <= 0:
                split = min(6000, len(text))
            part, text = text[:split], text[split:]
            parts.append({"page": page_no, "source": part, "translation": ""})
    return parts


def draft_pdf(state):
    if not state.get("parts") or any(not p["translation"].strip() for p in state["parts"]):
        raise ValueError("Перевод неполный: заполните все части перед созданием PDF.")
    pdf.init_font()
    font = pdf.pdfmetrics.getFont("DejaVu").face.charToGlyph
    if any(ord(c) not in font and not c.isspace() for p in state["parts"] for c in p["translation"]):
        raise ValueError("В переводе есть символы, отсутствующие в шрифте PDF. Замените их перед подтверждением.")
    if state.get("source_options", {}).get("placement") == "preserve":
        from .translation_layout import render
        return render(state)
    stream = BytesIO()
    style = ParagraphStyle("Translation", fontName="DejaVu", fontSize=11, leading=16, spaceAfter=9,
                           splitLongWords=True)
    note = ParagraphStyle("Page", parent=style, fontSize=8, leading=12, textColor="#626a75")
    story = []
    current = None
    for part in state["parts"]:
        if current != part["page"]:
            if current is not None:
                story.append(PageBreak())
            current = part["page"]
            scope = "Selected text · " if state.get("source_options") else ""
            story += [Paragraph(f"Translation · {escape(state['target'])} · {scope}Source page {current}", note), Spacer(1, 12)]
        for para in part["translation"].split("\n"):
            story.append(Paragraph(escape(para).replace("\t", "&#160;"*4) or "&#160;", style))
    SimpleDocTemplate(stream, pagesize=(595.28, 841.89), leftMargin=48, rightMargin=48,
                      topMargin=42, bottomMargin=42, title="Reviewed translation").build(story)
    return stream.getvalue()


class Translations:
    def __init__(self, store, translator=None):
        self.store = store
        self.translator = translator or translation_cli.translate
        self.check_auth = translator is None
        self.active = {}

    def path(self, pid, did):
        if not re.fullmatch(r"[a-f0-9]{32}", did):
            raise ValueError("Некорректный документ.")
        return self.store.folder(pid) / f"translation-{did}.json"

    def read(self, pid, did):
        path = self.path(pid, did)
        return json.loads(path.read_text("utf-8")) if path.exists() else None

    def write(self, pid, did, state):
        state["revision"] = state.get("revision", 0) + 1
        path = self.path(pid, did)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)

    def source(self, pid, did, options=None):
        with LOCK:
            p = self.store.load(pid)
            d = self.store.document(p, did)
            if d["mode"] != "prepare":
                raise ValueError("Сначала включите режим подготовки приложения.")
            saved_path = self.path(pid, did).with_name(f"translation-source-{did}.json")
            if options is None and saved_path.exists():
                saved = json.loads(saved_path.read_text("utf-8"))
                if saved.get("hash") == d["original"]["sha256"]:
                    options = saved["options"]
            options, parts = translation_source.extract(self.store.source(p, d["original"]), options, MAX_TEXT)
            return {"hash": d["original"]["sha256"], "parts": parts, "options": options,
                    "token": digest({"hash": d["original"]["sha256"], "parts": parts, "options": options})}

    def preview_source(self, pid, did, options=None):
        with LOCK:
            result = self.source(pid, did, options)
            path = self.path(pid, did).with_name(f"translation-source-{did}.json")
            temp = path.with_suffix(".tmp")
            with temp.open("w", encoding="utf-8") as f:
                json.dump({"hash": result["hash"], "options": result["options"]}, f)
                f.flush(); os.fsync(f.fileno())
            os.replace(temp, path)
            return result

    def state(self, pid, did):
        with LOCK:
            p = self.store.load(pid)
            d = self.store.document(p, did)
            state = self.read(pid, did)
            if state and state["status"] == "running" and (pid, did) not in self.active:
                state.update(status="interrupted", error="Приложение перезапустилось. Сохранённые части доступны; продолжите перевод.")
                self.write(pid, did, state)
            result = deepcopy(state)
            if result:
                result["stale"] = result["source_hash"] != d["original"]["sha256"]
            return result

    def start(self, pid, did, body):
        provider, target = body.get("provider"), body.get("target")
        if provider not in ("claude", "codex") or target not in LANGUAGES:
            raise ValueError("Выберите провайдера и язык перевода.")
        if body.get("reviewed_source") is not True:
            raise ValueError("Проверьте извлечённый текст и подтвердите отправку провайдеру.")
        with LOCK:
            if self.active:
                raise ValueError("Другой перевод уже выполняется. Дождитесь завершения или отмените его.")
            source = self.source(pid, did, body.get("options"))
            if body.get("token") != source["token"]:
                raise ValueError("Оригинал изменился. Обновите и проверьте извлечённый текст.")
            old = self.state(pid, did)
            if old and body.get("revision") != old["revision"]:
                raise ValueError("Черновик изменён в другом окне. Откройте перевод заново.")
            if body.get("resume"):
                if not old or old["stale"] or old["provider"] != provider or old["target"] != target:
                    raise ValueError("Продолжение возможно только с тем же оригиналом, языком и провайдером.")
                if old.get("source_token") != source["token"]:
                    raise ValueError("Выбор для перевода изменён. Верните прежний выбор или начните новый черновик.")
                state = self.read(pid, did)
                if all(x["translation"].strip() for x in state["parts"]):
                    raise ValueError("Все части уже заполнены. Проверьте перевод или начните заново.")
            else:
                if old and body.get("replace_draft") is not True:
                    raise ValueError("Подтвердите замену черновика. Прикреплённый PDF сохранится.")
                state = {"parts": source["parts"], "source_hash": source["hash"], "provider": provider,
                         "target": target, "source_options": source["options"], "source_token": source["token"],
                         "revision": old["revision"] if old else 0}
            state.update(status="running", error="", job=uuid4().hex)
            self.write(pid, did, state)
            event = Event()
            thread = Thread(target=self.worker, args=(pid, did, state["job"], event), daemon=True)
            self.active[pid, did] = (event, thread)
            thread.start()
            return self.state(pid, did)

    def worker(self, pid, did, job, cancel):
        try:
            with LOCK:
                state = self.read(pid, did)
            if self.check_auth and not translation_cli.status(state["provider"])["ready"]:
                raise ValueError("Вход через подписку не подтверждён. Проверьте подключение в официальном CLI и повторите.")
            for index, part in enumerate(state["parts"]):
                if cancel.is_set():
                    raise translation_cli.Cancelled()
                if part["translation"].strip():
                    continue
                text = self.translator(state["provider"], part["source"], state["target"], cancel)
                with LOCK:
                    current = self.read(pid, did)
                    if current["job"] != job or cancel.is_set():
                        raise translation_cli.Cancelled()
                    doc = self.store.document(self.store.load(pid), did)
                    if doc["original"]["sha256"] != state["source_hash"]:
                        raise ValueError("Оригинал заменён. Старый черновик сохранён, но его нельзя применить к новому файлу.")
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("Провайдер вернул пустую часть. Повторите оставшиеся части.")
                    current["parts"][index]["translation"] = text
                    self.write(pid, did, current)
            self.finish(pid, did, job, "complete", "")
        except translation_cli.Cancelled:
            self.finish(pid, did, job, "cancelled", "Перевод остановлен. Готовые части сохранены.")
        except Exception as exc:
            self.finish(pid, did, job, "error", str(exc) if isinstance(exc, ValueError) else "Перевод прерван из-за ошибки CLI. Готовые части сохранены; повторите оставшиеся.")
        finally:
            with LOCK:
                self.active.pop((pid, did), None)

    def finish(self, pid, did, job, status, message):
        with LOCK:
            state = self.read(pid, did)
            if state and state["job"] == job:
                state.update(status=status, error=message)
                self.write(pid, did, state)

    def cancel(self, pid, did):
        with LOCK:
            if (pid, did) in self.active:
                self.active[pid, did][0].set()
            return self.state(pid, did)

    def editable(self, pid, did, body):
        state = self.state(pid, did)
        if not state or state["status"] == "running":
            raise ValueError("Дождитесь завершения перевода или отмените его.")
        if body.get("revision") != state["revision"]:
            raise ValueError("Черновик изменён в другом окне. Откройте перевод заново.")
        if state["stale"]:
            raise ValueError("Оригинал заменён. Для нового файла нужен новый перевод.")
        return self.read(pid, did)

    def save(self, pid, did, body):
        with LOCK:
            state = self.editable(pid, did, body)
            texts = body.get("texts")
            if not isinstance(texts, list) or len(texts) != len(state["parts"]) or any(not isinstance(t, str) or len(t) > 48_000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", t) for t in texts):
                raise ValueError("Передайте текст каждой части (до 48 000 знаков), без управляющих символов.")
            for part, text in zip(state["parts"], texts):
                part["translation"] = text
            state["status"] = "complete" if all(t.strip() for t in texts) else "partial"
            state["error"] = ""
            self.write(pid, did, state)
            return self.state(pid, did)

    def preview(self, pid, did, revision):
        with LOCK:
            state = self.editable(pid, did, {"revision": revision})
        return draft_pdf(state)

    def apply(self, pid, did, body):
        with LOCK:
            state = self.editable(pid, did, body)
            if body.get("confirmed") is not True:
                raise ValueError("Подтвердите проверку перевода перед прикреплением PDF.")
            p = self.store.load(pid)
            self.store.upload(p, "Translation.pdf", draft_pdf(state), "translation", did)
            self.store.approve(p, did, "translation")
            state["applied_revision"] = state["revision"]
            self.write(pid, did, state)
            return self.store.public(p)
