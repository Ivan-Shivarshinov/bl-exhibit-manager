"""User-selected structural copies with source and page provenance."""
from io import BytesIO
import json
from urllib.parse import urlsplit
from zipfile import ZIP_STORED, ZipFile

from pypdf import PdfReader, PdfWriter

from . import material_text, pdf
from .project import LOCK


MAX_OUTPUT = 50_000_000
MAX_INPUT = 150_000_000
MAX_PAGES = 1000


def selected(value, length):
    if (not isinstance(value, list) or not 1 <= len(value) <= MAX_PAGES
            or any(type(page) is not int or not 1 <= page <= length for page in value)):
        raise ValueError('Укажите существующие страницы внутри PDF.')
    return value


def check_copy(reader, structural):
    if pdf.has_forms_or_signatures(reader):
        raise ValueError('Операция недоступна для PDF с формой или подписью. Исходник сохранён.')
    if not structural:
        return
    root = reader.root_object
    outline = root['/Outlines'] if '/Outlines' in root else {}
    empty_outline = isinstance(outline, dict) and not (set(outline) - {'/Type', '/Count'}) and outline.get('/Count', 0) == 0
    if not empty_outline or set(root) & {'/Dests', '/Names', '/OpenAction', '/AA', '/StructTreeRoot', '/OCProperties', '/PageLabels'}:
        raise ValueError('Для объединения и разделения этот PDF содержит неподдержанную структуру или навигацию. Исходник сохранён.')
    for page in reader.pages:
        if '/AA' in page:
            raise ValueError('Для выбранной операции PDF содержит неподдержанные действия страницы.')
        for link in page.get('/Annots', []):
            info = link.get_object()
            action = info.get('/A')
            if action is not None:
                action = action.get_object()
            uri = urlsplit(str(action.get('/URI', ''))) if action else None
            if (info.get('/Subtype') != '/Link' or '/Dest' in info or '/AA' in info or not action
                    or action.get('/S') != '/URI' or '/Next' in action
                    or uri.scheme not in ('http', 'https') or not uri.hostname):
                raise ValueError('Нельзя безопасно перенести часть ссылок или аннотаций. Исходник сохранён.')


def output_pdf(writer, operation, sources):
    records = json.dumps(sources, ensure_ascii=True, separators=(',', ':'))
    writer.add_metadata({'/ExhibitManagerOperation': operation, '/ExhibitManagerSources': records})
    output = BytesIO()
    writer.write(output)
    if output.tell() > MAX_OUTPUT:
        raise ValueError('Производная превышает 50 МБ. Уменьшите выбранный диапазон.')
    return output.getvalue()


def make_derivative(store, pid, operation, sources, groups=None, angle=None):
    if operation not in ('rotate', 'merge', 'split') or not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        raise ValueError('Выберите вид операции и до 20 исходных PDF.')
    if (operation == 'merge' and len(sources) < 2) or (operation != 'merge' and len(sources) != 1):
        raise ValueError('Для разделения или поворота нужен один PDF, для объединения — от двух.')
    with LOCK:
        project = store.load(pid)
        snapshots = []
        for item in sources:
            if not isinstance(item, dict) or not isinstance(item.get('source'), str):
                raise ValueError('Выберите текущую версию каждого исходного PDF.')
            source = material_text.page_source(store, project, item.get('did'), item.get('part'), 1, item['source'])
            snapshots.append((source, item.get('pages')))
    verified, size = [], 0
    for source, pages in snapshots:
        data = store.source(project, source)
        size += len(data)
        if size > MAX_INPUT:
            raise ValueError('Исходные PDF превышают общий предел 150 МБ.')
        reader = PdfReader(BytesIO(data), strict=True)
        check_copy(reader, operation != 'rotate')
        verified.append((source['sha256'], reader, pages))

    if operation == 'rotate':
        digest, reader, pages = verified[0]
        if type(angle) is not int or angle not in (90, 180, 270):
            raise ValueError('Выберите угол поворота.')
        numbers = selected(pages, len(reader.pages))
        if len(set(numbers)) != len(numbers):
            raise ValueError('Каждую страницу можно повернуть только один раз за операцию.')
        writer = PdfWriter(clone_from=reader)
        for number in numbers:
            writer.pages[number - 1].rotate(angle)
        return output_pdf(writer, operation, [{'sha256': digest, 'pages': numbers, 'angle': angle}])

    if operation == 'merge':
        selections = [(digest, reader, selected(pages, len(reader.pages))) for digest, reader, pages in verified]
        if sum(len(pages) for _, _, pages in selections) > MAX_PAGES:
            raise ValueError('В одном PDF может быть до 1000 страниц.')
        writer = PdfWriter()
        provenance = []
        for digest, reader, pages in selections:
            writer.append(reader, pages=[p - 1 for p in pages], import_outline=False)
            provenance.append({'sha256': digest, 'pages': pages})
        return output_pdf(writer, operation, provenance)

    digest, reader, _ = verified[0]
    if not isinstance(groups, list) or not 2 <= len(groups) <= 20:
        raise ValueError('Для разделения укажите от 2 до 20 частей.')
    groups = [selected(group, len(reader.pages)) for group in groups]
    if sorted(page for group in groups for page in group) != list(range(1, len(reader.pages) + 1)):
        raise ValueError('При разделении каждая страница должна входить ровно в одну часть.')
    data = BytesIO()
    with ZipFile(data, 'w', ZIP_STORED) as archive:
        for index, pages in enumerate(groups, 1):
            writer = PdfWriter()
            writer.append(reader, pages=[p - 1 for p in pages], import_outline=False)
            archive.writestr(f'Part-{index:02d}.pdf', output_pdf(writer, operation, [{'sha256': digest, 'pages': pages}]))
            if data.tell() > MAX_INPUT:
                raise ValueError('ZIP с частями превышает 150 МБ.')
    return data.getvalue()
