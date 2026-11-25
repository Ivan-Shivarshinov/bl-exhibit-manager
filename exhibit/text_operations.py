"""Resumable page work. The durable unit is one checked page."""
from contextlib import closing

from . import material_text, pdf
from .project import LOCK
from .text_index import open_index


MAX_BATCH = 100
MAX_INDEX_PAGES = 10_000


def checked_pages(store, pid, did, part, source, pages, language):
    if (not isinstance(pages, list) or not 1 <= len(pages) <= MAX_BATCH
            or any(type(page) is not int for page in pages) or len(set(pages)) != len(pages)
            or language not in material_text.LANGUAGES or not isinstance(source, str)):
        raise ValueError('Выберите от 1 до 100 разных страниц, исходник и язык OCR.')
    with LOCK:
        project = store.load(pid)
        for page in pages:
            material_text.page_source(store, project, did, part, page, source)


def ocr_pages(store, pid, did, part, source, pages, language, cancel, progress):
    checked_pages(store, pid, did, part, source, pages, language)
    failed, reused, completed, embedded = [], 0, 0, 0
    for i, page in enumerate(pages):
        if cancel():
            break
        progress(i, len(pages), f'Проверяем страницу {page}')
        try:
            current = material_text.page_status(store, pid, did, part, page, source)
            if current['record']:
                reused += 1
            elif current['embedded']:
                embedded += 1
            else:
                material_text.recognize(store, pid, did, part, page, source, language, cancel=cancel)
            completed += 1
        except (ValueError, OSError, pdf.pdfium.PdfiumError) as error:
            if cancel():
                break
            failed.append({'page': page, 'message': str(error) if isinstance(error, ValueError) else 'Страница недоступна.'})
        progress(i + 1, len(pages), f'Страниц проверено: {i + 1}')
    return {'failed': failed, 'completed': completed, 'reused': reused, 'embedded': embedded,
            'remaining': len(pages) - completed - len(failed)}


def index_pages(store, pid, cancel, progress):
    with LOCK:
        project = store.load(pid)
    files = {source['sha256']: source for doc in project['documents'] for role in ('original', 'translation')
             if (source := doc.get(role))}
    total = sum(source['pages'] for source in files.values())
    if total > MAX_INDEX_PAGES:
        raise ValueError('Для одной индексации доступно до 10 000 страниц. Поиск по частям остаётся доступен.')
    checked = reused = unavailable = 0
    with open_index(store.root) as index:
        if index.connection is None:
            raise ValueError('Не удалось открыть индекс. Проверьте место и доступ к папке данных.')
        for source_hash, source in files.items():
            if cancel():
                break
            try:
                data = store.source(project, source)
                with pdf.PDF_LOCK:
                    document = pdf.pdfium.PdfDocument(data)
            except (OSError, ValueError, pdf.pdfium.PdfiumError):
                checked += source['pages']
                unavailable += source['pages']
                continue
            try:
                for page_number in range(1, source['pages'] + 1):
                    if cancel():
                        break
                    if index.get(source_hash, page_number) is not None:
                        reused += 1
                    else:
                        try:
                            with pdf.PDF_LOCK, closing(document[page_number - 1]) as page, closing(page.get_textpage()) as text:
                                value = material_text.embedded_value(text)
                        except (ValueError, pdf.pdfium.PdfiumError):
                            unavailable += 1
                        else:
                            if not index.put(source_hash, page_number, value):
                                raise ValueError('Индекс достиг предела или недоступен для записи. Поиск по исходникам остаётся доступен.')
                    checked += 1
                    progress(checked, total, 'Индексируем текст PDF')
            finally:
                with pdf.PDF_LOCK:
                    document.close()
    return {'checked': checked, 'reused': reused, 'unavailable': unavailable, 'remaining': total - checked}
