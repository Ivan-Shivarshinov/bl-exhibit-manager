"""Build a separate searchable copy from reviewed page text and original images."""
from contextlib import closing
from io import BytesIO
import json

from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.pdfgen import canvas

from . import material_text, pdf
from .project import LOCK, digest


MAX_REVIEWED_TEXT = 20_000_000


def overlay(page, record):
    crop = page.cropbox
    width, height = float(crop.width), float(crop.height)
    data = BytesIO()
    layer = canvas.Canvas(data, pagesize=(width, height), invariant=1)
    with pdf.PDF_LOCK:
        pdf.init_font()
    for line in record['lines']:
        value = line['text'].strip()
        if not value:
            continue
        left, top, right, bottom = line['box']
        size = max(0.5, (bottom - top) * height * 0.8)
        text = layer.beginText(left * width, (1 - bottom) * height)
        text.setFont('DejaVu', size)
        text.setTextRenderMode(3)
        natural = layer.stringWidth(value, 'DejaVu', size)
        if natural:
            text.setHorizScale((right - left) * width / natural * 100)
        text.textOut(value)
        layer.drawText(text)
    layer.showPage()
    layer.save()
    page.merge_transformed_page(PdfReader(data).pages[0], Transformation().translate(float(crop.left), float(crop.bottom)))


def make_copy(store, pid, did, part, source_hash):
    with LOCK:
        project = store.load(pid)
        source = material_text.page_source(store, project, did, part, 1, source_hash)
        records = {}
        size = 0
        for number in range(1, source['pages'] + 1):
            if record := material_text.read_record(store, pid, source_hash, number):
                size += len(json.dumps(record, ensure_ascii=False).encode('utf-8'))
                if size > MAX_REVIEWED_TEXT:
                    raise ValueError('Распознанный текст превышает предел для поисковой копии.')
                records[number] = record
    data = store.source(project, source)
    reader = PdfReader(BytesIO(data), strict=True)
    if pdf.has_forms_or_signatures(reader):
        raise ValueError('Поисковая копия недоступна для PDF с формами или подписью. Используйте исходный PDF.')
    if not records:
        raise ValueError('Сначала распознайте и подтвердите страницы скана.')

    with pdf.PDF_LOCK:
        document = pdf.pdfium.PdfDocument(data)
    try:
        for number in range(1, source['pages'] + 1):
            record = records.get(number)
            if record is not None:
                if not record['confirmed']:
                    raise ValueError(f'Сначала подтвердите распознанный текст страницы {number}.')
                continue
            with pdf.PDF_LOCK, closing(document[number - 1]) as page, closing(page.get_textpage()) as text:
                if not material_text.embedded_value(text):
                    raise ValueError(f'Сначала распознайте и подтвердите страницу {number}.')
    finally:
        with pdf.PDF_LOCK:
            document.close()

    writer = PdfWriter(clone_from=reader)
    for number, record in records.items():
        page = writer.pages[number - 1]
        if page.rotation or page.user_unit != 1:
            raise ValueError(f'Поисковая копия пока недоступна для геометрии страницы {number}. Исходник сохранён.')
        overlay(page, record)
    writer.add_metadata({'/ExhibitManagerSourceSHA256': source_hash,
                         '/ExhibitManagerOCRSHA256': digest(records), '/ExhibitManagerOperation': 'searchable-copy'})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
