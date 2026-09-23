"""Stable citation identities shared by the Word pane and DOCX importer."""
import re
from urllib.parse import urlparse, parse_qs, urlencode
from uuid import uuid4

PATH = re.compile(r'/api/word/open/([a-f0-9]{32})/([a-f0-9]{32})/([a-f0-9]{32})')
DEFAULTS = {'name_form': 'full', 'separator': '; ', 'locator_separator': ', '}


def settings(project):
    return {**DEFAULTS, **project.get('citation_style', {})}


def check_settings(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('Неизвестные настройки сносок.')
    merged = {**DEFAULTS, **value}
    if merged['name_form'] not in ('full', 'short') or merged['separator'] not in ('; ', '. ') or merged['locator_separator'] not in (', ', ': '):
        raise ValueError('Некорректный формат сноски.')
    return merged


def title(project, document, form='full'):
    from .project import identifier
    if form not in ('full', 'short'):
        raise ValueError('Выберите полное или короткое название.')
    name = (document.get('short_title') if form == 'short' else '') or document['title']
    ident = identifier(document, project)
    return name if not ident or name.casefold().startswith(ident.casefold()+',') or name.casefold() == ident.casefold() else f'{ident}, {name}'


def link(origin, pid, did, text, form='full', cid=None):
    return f'{origin}/api/word/open/{pid}/{did}/{cid or uuid4().hex}?' + urlencode({'form': form, 'text': text})


def parse_link(value):
    try:
        url = urlparse(value)
        match = PATH.fullmatch(url.path)
        query = parse_qs(url.query)
        if url.scheme != 'https' or url.hostname not in ('localhost', '127.0.0.1') or not match:
            return None
        form, text = query.get('form', ['full'])[0], query.get('text', [''])[0]
        if form not in ('full', 'short') or not text or len(text) > 1000:
            return None
        return dict(zip(('project', 'document', 'citation'), match.groups()), form=form, expected=text)
    except ValueError:
        return None


def catalog(project):
    from .project import identifier, document_ready, digest
    documents = [{'id': d['id'], 'identifier': identifier(d, project), 'title': d['title'],
                  'short_title': d.get('short_title', ''), 'full': title(project, d),
                  'short': title(project, d, 'short'), 'ready': document_ready(project, d)}
                 for d in project['documents']]
    result = {'id': project['id'], 'name': project['name'], 'documents': documents, 'style': settings(project),
              'main_sha': (project.get('main') or {}).get('sha256', '')}
    return {**result, 'version': digest(result)}


def annotate(found, project):
    by_id = {d['id']: d for d in project['documents']}
    for ref in found['references']:
        if ref.get('managed') and ref['target'] in by_id:
            current = title(project, by_id[ref['target']], ref['managed']['form'])
            if ref['mention'] != current:
                ref['managed_current_text'] = current
                ref['scope_review'] = True


def paragraph_links(parts, paragraph, project_id):
    from . import word
    if not project_id or word.RELS not in parts:
        return []
    rels = {r.get('Id'): r.get('Target', '') for r in word.xml(parts[word.RELS])}
    position, found = 0, []
    for child in paragraph:
        text = word.text_of(child)
        if child.tag == f'{{{word.W}}}hyperlink':
            meta = parse_link(rels.get(child.get(f'{{{word.R}}}id'), ''))
            if meta and text:
                found.append({'start': position, 'end': position+len(text), 'mention': text,
                              'managed': meta, 'target': meta['document'] if meta['project'] == project_id else None})
        position += len(text)
    return found


def rewrite_citation_ooxml(data, expected, replacement, address, identifier=''):
    """Preserve uniform identifier/title typography; reject ambiguous mixed edits."""
    from copy import deepcopy
    from lxml import etree as E
    from . import word
    if not all(isinstance(v, str) for v in (data, expected, replacement, address, identifier)) or len(data) > 2_000_000:
        raise ValueError('Некорректный фрагмент Word.')
    if not expected or not replacement or len(replacement) > 1000 or not parse_link(address):
        raise ValueError('Некорректное обновление ссылки Word.')
    try:
        root = word.xml(data.encode('utf-8'))
    except E.XMLSyntaxError as exc:
        raise ValueError('Word вернул некорректный XML ссылки.') from exc
    pkg = 'http://schemas.microsoft.com/office/2006/xmlPackage'
    document = root.find(f"{{{pkg}}}part[@{{{pkg}}}name='/word/document.xml']")
    relpart = root.find(f"{{{pkg}}}part[@{{{pkg}}}name='/word/_rels/document.xml.rels']")
    if document is None or relpart is None:
        raise ValueError('Word вернул неподдерживаемую структуру ссылки.')
    links = document.findall('.//w:hyperlink', word.NS)
    if len(links) != 1 or word.text_of(links[0]) != expected:
        raise ValueError('Фрагмент ссылки изменился. Повторите проверку ссылок.')
    link = links[0]
    relation = next((r for r in relpart.iter() if r.tag == f'{{{word.REL}}}Relationship' and r.get('Id') == link.get(f'{{{word.R}}}id')), None)
    if relation is None:
        raise ValueError('Не найдена связь Word.')
    if expected != replacement:
        old_label = word.UNPREFIXED.match(expected) or word.IDENT.match(expected)
        old_end = old_label.end() if old_label and (old_label.end() == len(expected) or expected[old_label.end():].startswith(',')) else 0
        new_end = len(identifier) if identifier and (replacement == identifier or replacement.startswith(identifier+',')) else 0
        groups = [[], []]
        pos = 0
        for run in link:
            if run.tag != f'{{{word.W}}}r' or any(child.tag not in (f'{{{word.W}}}rPr', f'{{{word.W}}}t') for child in run):
                raise ValueError('Сложное оформление ссылки: обновите её вручную в Word. Документ не изменён.')
            length = len(word.text_of(run))
            props = run.find('w:rPr', word.NS)
            for i, (a, b) in enumerate(((0, old_end), (old_end, len(expected)))):
                if pos < b and pos+length > a:
                    groups[i].append(props)
            pos += length
        def signature(node):
            return None if node is None else (node.tag, tuple(sorted(node.attrib.items())), node.text, tuple(signature(c) for c in node))
        if any(len({signature(p) for p in group}) > 1 for group in groups):
            raise ValueError('Внутри названия смешанное оформление. Обновите эту ссылку вручную в Word; остальные ссылки не изменены.')
        # A title-only citation cannot acquire a new label without a style decision.
        if bool(old_end) != bool(new_end):
            raise ValueError('Изменился вид обозначения. Обновите эту ссылку вручную в Word.')
        for child in list(link): link.remove(child)
        for group, value in zip(groups, (replacement[:new_end], replacement[new_end:])):
            if not value: continue
            run = E.SubElement(link, f'{{{word.W}}}r')
            if group and group[0] is not None: run.append(deepcopy(group[0]))
            E.SubElement(run, f'{{{word.W}}}t', {'{http://www.w3.org/XML/1998/namespace}space':'preserve'}).text = value
    relation.set('Target', address)
    return E.tostring(root, encoding='unicode')
