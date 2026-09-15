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
    from .project import identifier, revision, digest
    documents = [{'id': d['id'], 'identifier': identifier(d, project), 'title': d['title'],
                  'short_title': d.get('short_title', ''), 'full': title(project, d),
                  'short': title(project, d, 'short'), 'ready': d['approved'] == revision(project, d)}
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
