"""Local source search and portable, user-reviewed OCR for individual PDF pages."""
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import Lock
from uuid import uuid4
import csv
import json
import os
import re
import shutil
import subprocess
import time

from PIL import Image

from . import external_process, pdf, schema
from .text_index import open_index
from .project import LOCK, identifier


OCR_MEMBER = r'ocr-([a-f0-9]{64})-([1-9][0-9]{0,3})\.json'
OCR_LIMIT = 1_000_000
OCR_LINES = 800
LANGUAGES = ('eng', 'rus')
OCR_SLOT = Lock()
PAGES_PER_SEARCH = 200
HITS_PER_SEARCH = 60
CHARS_PER_PAGE = 250_000


def executable():
    explicit = os.environ.get('EXHIBIT_TESSERACT')
    if explicit:
        path = Path(explicit)
        return str(path) if path.is_absolute() and path.is_file() else None
    found = shutil.which('tesseract')
    if found:
        return str(Path(found).resolve())
    if os.name == 'nt':
        candidates = [Path(root) / 'Tesseract-OCR/tesseract.exe'
                      for key in ('ProgramFiles', 'ProgramFiles(x86)') if (root := os.environ.get(key))]
    else:
        candidates = [Path('/opt/homebrew/bin/tesseract'), Path('/usr/local/bin/tesseract')]
    return next((str(path) for path in candidates if path.is_file()), None)


def run_engine(command, image=None, timeout=6, cancel=None):
    env = {**os.environ, 'OMP_THREAD_LIMIT': '2'}
    if language_folder := os.environ.get('EXHIBIT_TESSDATA'):
        env['TESSDATA_PREFIX'] = language_folder
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        with external_process.popen(command, env=env, stdin=subprocess.PIPE if image is not None else subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **flags) as process:
            deadline = time.monotonic() + timeout
            while True:
                if (cancel and cancel()) or time.monotonic() >= deadline:
                    process.kill()
                    process.communicate()
                    if cancel and cancel():
                        raise ValueError('Распознавание остановлено.')
                    raise ValueError('Локальное распознавание не завершилось вовремя. Попробуйте другую страницу.')
                try:
                    interval = min(.25, deadline - time.monotonic()) if cancel else deadline - time.monotonic()
                    output, _ = process.communicate(image, timeout=max(.001, interval))
                    break
                except subprocess.TimeoutExpired:
                    image = None
            if process.returncode:
                raise ValueError('Tesseract не обработал страницу. Проверьте установку и языковые данные.')
    except OSError:
        raise ValueError('Не удалось запустить локальный Tesseract.') from None
    if len(output) > OCR_LIMIT:
        raise ValueError('Результат распознавания страницы слишком большой.')
    return output.decode('utf-8', errors='replace')


def engine_languages(path):
    available = set(run_engine([path, '--list-langs']).splitlines())
    return [language for language in LANGUAGES if language in available]


def engine_status():
    path = executable()
    if path is None:
        return {'available': False, 'languages': [], 'message': 'Установите Tesseract и нужные языки для OCR.'}
    try:
        languages = engine_languages(path)
    except ValueError as exc:
        return {'available': False, 'languages': [], 'message': str(exc)}
    return {'available': bool(languages), 'languages': languages,
            'message': '' if languages else 'Установите языковые данные Tesseract для английского или русского.'}


def record_name(digest, number):
    return f'ocr-{digest}-{number}.json'


def check_record(record, digest, number):
    error = 'Повреждена запись распознавания PDF.'
    if (not isinstance(record, dict) or set(record) != {'format', 'source', 'page', 'language', 'revision', 'confirmed', 'lines'}
            or type(record['format']) is not int or record['format'] != 1 or record['source'] != digest
            or type(record['page']) is not int or record['page'] != number or not 1 <= number <= 1000
            or record['language'] not in LANGUAGES or not isinstance(record['revision'], str)
            or not re.fullmatch(r'[a-f0-9]{32}', record['revision']) or type(record['confirmed']) is not bool
            or not isinstance(record['lines'], list) or not 0 < len(record['lines']) <= OCR_LINES):
        raise ValueError(error)
    for line in record['lines']:
        if (not isinstance(line, dict) or set(line) != {'text', 'box', 'confidence'}
                or not isinstance(line['text'], str) or len(line['text']) > 1000 or '\n' in line['text'] or '\r' in line['text']
                or type(line['confidence']) is not int or not 0 <= line['confidence'] <= 100
                or not isinstance(line['box'], list) or len(line['box']) != 4
                or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in line['box'])):
            raise ValueError(error)
        left, top, right, bottom = line['box']
        if left >= right or top >= bottom:
            raise ValueError(error)
    if not any(line['text'].strip() for line in record['lines']):
        raise ValueError('В распознанных строках нет текста.')
    return record


def read_record(store, pid, digest, number):
    path = store.folder(pid) / record_name(digest, number)
    try:
        with path.open('rb') as stream:
            raw = stream.read(OCR_LIMIT + 1)
        if len(raw) > OCR_LIMIT:
            raise ValueError('Файл распознавания слишком большой.')
        return check_record(json.loads(raw), digest, number)
    except FileNotFoundError:
        return None
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise ValueError('Не удалось прочитать сохранённый текст OCR.') from exc


def page_source(store, project, did, part, number, digest=None):
    doc = store.document(project, did)
    if part not in ('original', 'translation') or not (source := doc.get(part)):
        raise ValueError('Выберите доступный PDF.')
    if digest is not None and source['sha256'] != digest:
        raise ValueError('Исходник изменился. Откройте актуальную страницу.')
    if type(number) is not int or not 1 <= number <= source['pages']:
        raise ValueError('Страница вне документа.')
    return source


def page_status(store, pid, did, part, number, digest):
    with LOCK:
        project = store.load(pid)
        source = page_source(store, project, did, part, number, digest)
        record = read_record(store, pid, source['sha256'], number)
    return {'source': source['sha256'], 'embedded': False if record else has_embedded(store.source(project, source), number),
            'record': record}


def page_image(store, pid, did, part, number, digest):
    with LOCK:
        project = store.load(pid)
        source = page_source(store, project, did, part, number, digest)
    return pdf.render_png(store.source(project, source), number)


def tsv_lines(tsv, width, height):
    groups = defaultdict(list)
    rows = csv.DictReader(tsv.splitlines(), delimiter='\t', quoting=csv.QUOTE_NONE)
    try:
        for row in rows:
            text = (row.get('text') or '').strip()
            if row.get('level') != '5' or not text:
                continue
            left, top, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
            box = [max(0, left) / width, max(0, top) / height,
                   min(width, left + w) / width, min(height, top + h) / height]
            if box[0] < box[2] and box[1] < box[3]:
                key = tuple(row[k] for k in ('block_num', 'par_num', 'line_num'))
                groups[key].append((text, box, min(100, max(0, round(float(row['conf']))))))
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Tesseract вернул повреждённые координаты распознавания.') from exc
    return [{'text': ' '.join(word[0] for word in words), 'box': union([word[1] for word in words]),
             'confidence': min(word[2] for word in words)} for words in groups.values()]


def union(rectangles):
    return [min(r[0] for r in rectangles), min(r[1] for r in rectangles),
            max(r[2] for r in rectangles), max(r[3] for r in rectangles)]


def recognize(store, pid, did, part, number, digest, language, cancel=None):
    if not isinstance(digest, str) or language not in LANGUAGES:
        raise ValueError('Укажите исходник и язык распознавания.')
    with LOCK:
        project = store.load(pid)
        source = page_source(store, project, did, part, number, digest)
        old = read_record(store, pid, digest, number)
        if old:
            return old
    if not OCR_SLOT.acquire(blocking=False):
        raise ValueError('Уже распознаётся другая страница. Повторите после завершения.')
    try:
        if cancel and cancel():
            raise ValueError('Распознавание остановлено.')
        data = store.source(project, source)
        if has_embedded(data, number):
            raise ValueError('На странице есть встроенный текст. Найдите его через поиск.')
        path = executable()
        if not path or language not in engine_languages(path):
            raise ValueError('Для выбранного языка требуется локальный Tesseract и соответствующий языковой пакет.')
        png = pdf.render_png(data, number, 300 / 72)
        with Image.open(BytesIO(png)) as image:
            width, height = image.size
        result = run_engine([path, 'stdin', 'stdout', '-l', language, '--dpi', '300',
                             '-c', 'tessedit_create_tsv=1'], png, timeout=75, cancel=cancel)
        lines = tsv_lines(result, width, height)
        if not lines:
            raise ValueError(f'На странице {number} не найден читаемый текст. Проверьте ориентацию и язык.')
        record = check_record({'format': 1, 'source': digest, 'page': number, 'language': language,
                               'revision': uuid4().hex, 'confirmed': False, 'lines': lines}, digest, number)
        with LOCK:
            if cancel and cancel():
                raise ValueError('Распознавание остановлено.')
            current = store.load(pid)
            latest = page_source(store, current, did, part, number, digest)
            store.source(current, latest)
            if saved := read_record(store, pid, digest, number):
                return saved
            schema.upgrade(store, current, target=max(current['schema'], 5))
            save_record(store, pid, record)
            return record
    finally:
        OCR_SLOT.release()


def save_record(store, pid, record):
    from .library import atomic_json
    name = record_name(record['source'], record['page'])
    if len(json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > OCR_LIMIT:
        raise ValueError('Файл распознавания слишком большой.')
    try:
        atomic_json(store.folder(pid) / name, record)
    except OSError as exc:
        raise ValueError('Не удалось сохранить OCR. Проверьте свободное место и доступ к папке.') from exc


def confirm(store, pid, did, part, number, digest, revision, texts):
    if not isinstance(digest, str) or not isinstance(revision, str) or not isinstance(texts, list):
        raise ValueError('Укажите версию и исправленный текст.')
    with LOCK:
        project = store.load(pid)
        source = page_source(store, project, did, part, number, digest)
        store.source(project, source)
        record = read_record(store, pid, digest, number)
        if record is None or record['revision'] != revision:
            raise ValueError('Текст уже изменился. Откройте свежую версию страницы.')
        if len(texts) != len(record['lines']) or any(not isinstance(text, str) or len(text) > 1000 for text in texts):
            raise ValueError('Сохраните число строк. Каждая строка должна содержать до 1000 символов.')
        if record['confirmed'] and [line['text'] for line in record['lines']] == texts:
            return record
        for line, text in zip(record['lines'], texts):
            line['text'] = text
        record.update(revision=uuid4().hex, confirmed=True)
        check_record(record, digest, number)
        save_record(store, pid, record)
        return record


def terms_for(query):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 160 or '\x00' in query:
        raise ValueError('Введите поисковый запрос до 160 символов.')
    terms = [quoted or single for quoted, single in re.findall(r'"([^"\n]+)"|([^\s"]+)', query.strip())]
    if not 1 <= len(terms) <= 12 or any(not term.strip() for term in terms) or query.count('"') % 2:
        raise ValueError('Укажите до 12 слов. Заключайте фразу в парные кавычки.')
    return terms


def patterns_for(terms, match_case):
    flags = 0 if match_case else re.IGNORECASE
    return [re.compile(r'\s+'.join(re.escape(word) for word in term.split()), flags) for term in terms]


def matching_span(text, patterns):
    first, *remaining = patterns
    match = first.search(text)
    if match and all(pattern.search(text) for pattern in remaining):
        return match.span()
    return None


def inside(box, rect):
    return rect is None or (box[0] >= rect[0] and box[1] >= rect[1] and box[2] <= rect[2] and box[3] <= rect[3])


def in_fragment(box, rect):
    if rect is None:
        return box
    left, top, right, bottom = rect
    return [(box[0] - left) / (right - left), (box[1] - top) / (bottom - top),
            (box[2] - left) / (right - left), (box[3] - top) / (bottom - top)]


def ocr_match(record, patterns, rect=None):
    lines = [line if inside(line['box'], rect) else {'text': '\x00', 'box': None} for line in record['lines']]
    text = ' '.join(line['text'] for line in lines)
    if not (span := matching_span(text, patterns)):
        return None
    start, end = span
    offset = 0
    found = []
    for line in lines:
        length = len(line['text'])
        if line['box'] is not None and offset < end and offset + length > start:
            found.append(line)
        offset += length + 1
        if offset >= end:
            break
    return {'context': ' '.join(line['text'] for line in found)[:300], 'boxes': [in_fragment(line['box'], rect) for line in found],
            'method': 'confirmed-ocr' if record['confirmed'] else 'ocr'}


def text_count(text):
    count = text.count_chars()
    if count > CHARS_PER_PAGE:
        raise ValueError('Слишком большой текстовый слой страницы.')
    return count


def embedded_value(text):
    count = text_count(text)
    value = text.get_text_range(0, count, errors='replace') if count else ''
    return value if '\ufffd' not in value and any(char.isalnum() for char in value) else ''


def has_embedded(data, number):
    try:
        with pdf.PDF_LOCK, closing(pdf.pdfium.PdfDocument(data)) as doc:
            with closing(doc[number - 1]) as page, closing(page.get_textpage()) as text:
                return bool(embedded_value(text))
    except pdf.pdfium.PdfiumError as exc:
        raise ValueError(f'Не удалось прочитать страницу {number}.') from exc


def rectangle_indices(text, value, start, end):
    start_units = len(value[:start].encode('utf-16-le')) // 2
    end_units = start_units + len(value[start:end].encode('utf-16-le')) // 2
    char_index = pdf.pdfium.raw.FPDFText_GetCharIndexFromTextIndex
    indices = [char_index(text, i) for i in range(start_units, end_units)]
    valid = [index for index in indices if index >= 0]
    return (min(valid), max(valid) - min(valid) + 1) if valid else None


def normalize_box(conv, rectangle, width, height, *, clamp=True):
    left, bottom, right, top = rectangle
    corners = [conv.to_bitmap(x, y) for x in (left, right) for y in (bottom, top)]
    xs = [x / width for x, _ in corners]
    ys = [y / height for _, y in corners]
    box = [min(xs), min(ys), max(xs), max(ys)]
    return [min(1, max(0, value)) for value in box] if clamp else box


def fragment_text(page, text, value, rect):
    # PageToDevice uses integer output. Use a fine virtual surface and inset by one
    # pixel, so rounding cannot import a character straddling the visible edge.
    width, height = (max(1, round(size * 10)) for size in page.get_size())
    conv = pdf.pdfium.PdfPosConv(page, (0, 0, width, height, 0))
    safe = [rect[0] + 1 / width, rect[1] + 1 / height, rect[2] - 1 / width, rect[3] - 1 / height]
    chars, boxes = [], []
    index_for = pdf.pdfium.raw.FPDFText_GetCharIndexFromTextIndex
    offset = 0
    for char in value:
        index = index_for(text, offset)
        offset += 2 if ord(char) > 0xffff else 1
        if char.isspace():
            chars.append(char)
            boxes.append(None)
            continue
        box = normalize_box(conv, text.get_charbox(index), width, height, clamp=False) if index >= 0 else None
        accepted = box is not None and inside(box, safe)
        chars.append(char if accepted else '\x00')
        boxes.append(in_fragment(box, rect) if accepted else None)
    return ''.join(chars), boxes


def match_context(value, start, end):
    # The sentinel represents an excluded region. Never copy it into the result.
    left = max(0, start - 60, value.rfind('\x00', 0, start) + 1)
    boundary = value.find('\x00', end)
    right = min(end + 80, boundary) if boundary >= 0 else end + 80
    return ' '.join(value[left:right].split())


def native_match(document, number, patterns, rect=None, index=None, source=None):
    with pdf.PDF_LOCK:
        with closing(document[number - 1]) as page, closing(page.get_textpage()) as text:
            value = embedded_value(text)
            if index is not None and index.get(source, number) is None:
                index.put(source, number, value)
            if not value:
                return False, None
            if rect is not None:
                value, coordinates = fragment_text(page, text, value, rect)
                if not any(char.isalnum() for char in value):
                    return False, None
                hit = matching_span(value, patterns)
                if not hit:
                    return True, None
                start, end = hit
                boxes = [box for box in coordinates[start:end] if box]
                return True, {'context': match_context(value, start, end), 'boxes': boxes, 'method': 'embedded'}
            if not (span := matching_span(value, patterns)):
                return True, None
            start, end = span
            snippet = match_context(value, start, end)
            indices = rectangle_indices(text, value, start, end)
            rectangles = [text.get_rect(i) for i in range(text.count_rects(*indices))] if indices else []
            with closing(page.render(scale=pdf.raster_scale(*page.get_size(), 1), limit_image_cache=True)) as bitmap:
                conv = bitmap.get_posconv(page)
                boxes = [normalize_box(conv, rectangle, bitmap.width, bitmap.height) for rectangle in rectangles]
            return True, {'context': snippet, 'boxes': [b for b in boxes if b[0] < b[2] and b[1] < b[3]], 'method': 'embedded'}


@dataclass
class SearchPdf:
    identity: dict
    units: list
    load: object
    review: object = None


def search_sources(files, entries, query, match_case, offset, snapshot, index):
    terms = terms_for(query)
    if type(match_case) is not bool or type(offset) is not int or offset < 0:
        raise ValueError('Некорректные параметры поиска.')
    patterns = patterns_for(terms, match_case)
    binding = [[file.identity, file.units] for file in files]
    binding.extend(entry['identity'] for entry in entries)
    current_snapshot = sha256(json.dumps([query, match_case, binding], ensure_ascii=False).encode('utf-8')).hexdigest()
    if offset and snapshot != current_snapshot:
        raise ValueError('Состав подачи, библиотеки или запрос изменился. Запустите поиск заново.')
    total = sum(len(file.units) for file in files) + len(entries)
    if offset > total:
        raise ValueError('Смещение вне результатов поиска.')
    seen = scanned = unread = skipped = 0
    hits, errors = [], []
    deadline = time.monotonic() + 9

    def exhausted():
        return seen >= PAGES_PER_SEARCH or len(hits) >= HITS_PER_SEARCH or (seen and time.monotonic() >= deadline)

    for file in files:
        if skipped + len(file.units) <= offset:
            skipped += len(file.units)
            continue
        if exhausted():
            break
        document = None
        try:
            data = file.load()
        except (ValueError, OSError):
            data = None
        try:
            for unit in file.units[max(0, offset - skipped):]:
                if exhausted():
                    break
                seen += 1
                page = unit['page']
                rect = unit.get('rect')
                try:
                    if data is None:
                        raise ValueError('Исходник недоступен.')
                    if file.review and (record := file.review(page)):
                        result = ocr_match(record, patterns, rect)
                        scanned += bool(rect is not None and not any(inside(line['box'], rect) for line in record['lines']))
                    else:
                        source = file.identity['source']
                        cached = index.get(source, page)
                        if rect is None and cached is not None and not matching_span(cached, patterns):
                            scanned += not cached
                            continue
                        if document is None:
                            with pdf.PDF_LOCK:
                                document = pdf.pdfium.PdfDocument(data)
                        embedded, result = native_match(document, page, patterns, rect, index, source)
                        scanned += not embedded
                except (ValueError, OSError, pdf.pdfium.PdfiumError):
                    unread += 1
                    if len(errors) < 8:
                        errors.append({'title': file.identity['title'], 'page': page})
                    continue
                if result:
                    hits.append({**file.identity, **unit, **result})
            else:
                skipped += len(file.units)
                continue
            break
        finally:
            if document is not None:
                with pdf.PDF_LOCK:
                    document.close()

    if skipped == sum(len(file.units) for file in files):
        for entry in entries[max(0, offset - skipped):]:
            if exhausted():
                break
            seen += 1
            if match := matching_span(entry['text'], patterns):
                hits.append({**entry['identity'], 'context': match_context(entry['text'], *match),
                             'span': list(match), 'method': 'docx'})
    return {'hits': hits, 'pages': total, 'checked': seen, 'without_text': scanned, 'unavailable': unread,
            'errors': errors, 'snapshot': current_snapshot, 'next_offset': offset + seen if offset + seen < total else None}


def word_entries(data, source):
    from . import word
    parts = word.package(data)
    document = word.xml(parts['word/document.xml'])
    rows = [(f'body-{i}', f'Абзац {i + 1}', node)
            for i, node in enumerate(document.findall('./w:body//w:p', word.NS))]
    _, notes = word.paragraphs(parts)
    rows.extend((f'note-{fid}-{pi}', f'Сноска {ordinal}, абзац {pi + 1}', node) for fid, ordinal, pi, node in notes)
    return [{'identity': {'part': 'main', 'source': source['sha256'], 'title': source['name'],
                          'location': location, 'label': label}, 'text': value}
            for location, label, node in rows if (value := word.text_of(node)).strip()]


def word_location(store, pid, source_hash, location):
    with LOCK:
        project = store.load(pid)
    source = project.get('main')
    if source is None or source['sha256'] != source_hash:
        raise ValueError('Основной DOCX изменился. Запустите поиск заново.')
    for entry in word_entries(store.source(project, source), source):
        if entry['identity']['location'] == location:
            return {**entry['identity'], 'text': entry['text']}
    raise ValueError('Указанное место отсутствует в DOCX.')


def selected_pages(doc):
    from .volumes import source_pages
    return source_pages(doc)


def search_project(store, pid, query, part='all', match_case=False, offset=0, snapshot=None, scope='source'):
    if part not in ('all', 'original', 'translation') or scope not in ('source', 'prepared', 'docx'):
        raise ValueError('Некорректная область поиска.')
    with LOCK:
        project = store.load(pid)
    files, entries = [], []
    if scope == 'docx':
        if source := project.get('main'):
            entries = word_entries(store.source(project, source), source)
    else:
        for doc in project['documents']:
            prepared = selected_pages(doc) if scope == 'prepared' else None
            for role in ('original', 'translation'):
                if part not in ('all', role) or not (source := doc.get(role)):
                    continue
                units = [{'page': row['source_page'], 'prepared_page': row['prepared_page'], 'rect': row['rect']}
                         for row in prepared if row['part'] == role] if prepared is not None else [
                             {'page': page} for page in range(1, source['pages'] + 1)]
                if units:
                    identity = {'document': doc['id'], 'title': doc['title'], 'identifier': identifier(doc, project),
                                'part': role, 'source': source['sha256']}
                    files.append(SearchPdf(identity, units, partial(store.source, project, source),
                                           partial(read_record, store, pid, source['sha256'])))
    with open_index(store.root) as index:
        result = search_sources(files, entries, query, match_case, offset, snapshot, index)
    if scope == 'prepared' and result['hits']:
        from .submission_index import fingerprint
        from .volume_settings import settings
        from .volume_jobs import manager
        token = fingerprint(project)
        pages = manager(store).search_map(pid, token) if settings(project)['enabled'] else {}
        for hit in result['hits']:
            key = (hit['document'], hit['part'], hit['source'], hit['prepared_page'])
            if page := pages.get(key):
                hit['volume'] = {'token': token, 'number': page['volume'], 'page': page['local_page']}
    return result


def prepared_image(store, pid, did, part, page, source_hash, prepared_page, region):
    with LOCK:
        project = store.load(pid)
        source = page_source(store, project, did, part, page, source_hash)
        doc = store.document(project, did)
        row = next((row for row in selected_pages(doc) if row['prepared_page'] == prepared_page), None)
    if not row or (row['part'], row['source_page'], row['source_sha256'], row['rect']) != (part, page, source_hash, region):
        raise ValueError('Подготовленный фрагмент изменился. Запустите поиск заново.')
    return pdf.render_png(store.source(project, source), page, rect=region)


def read_library_pdf(library, lid, source):
    data = library.part(lid, source).read_bytes()
    if sha256(data).hexdigest() != source['sha256']:
        raise ValueError('Исходный PDF библиотеки изменился.')
    return data


def search_library(library, lid, query, match_case=False, offset=0, snapshot=None, history=False, hidden=False, tag=''):
    if type(history) is not bool or type(hidden) is not bool or not isinstance(tag, str) or len(tag) > 80:
        raise ValueError('Некорректные фильтры библиотеки.')
    with LOCK:
        catalog = library.load(lid)
    files = []
    for card in catalog['materials'].values():
        if card['hidden'] and not hidden:
            continue
        version_ids = card['versions'] if history else card['versions'][-1:]
        for vid in version_ids:
            version = library.version(lid, card['id'], vid, _catalog=catalog)
            if tag and tag not in version['metadata']['tags']:
                continue
            for role, source in version['parts'].items():
                identity = {'document': card['id'], 'title': version['metadata']['title'], 'part': role,
                            'source': source['sha256'], 'version': vid, 'version_number': card['versions'].index(vid) + 1,
                            'hidden': card['hidden']}
                records = {record['page']: record for record in version.get('ocr', {}).get(role, [])}
                files.append(SearchPdf(identity, [{'page': page} for page in range(1, source['pages'] + 1)],
                                       partial(read_library_pdf, library, lid, source), records.get))
    with open_index(library.store.root) as index:
        return search_sources(files, [], query, match_case, offset, snapshot, index)
