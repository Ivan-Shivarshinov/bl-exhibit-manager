"""Immutable exports: publish one complete snapshot directory by atomic rename."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import json
import os
import re


def manifest(store, project):
    from .project import identifier, revision, digest
    return {'main': (project.get('main') or {}).get('sha256'),
            'references': digest(project['references']),
            'documents': [{'id': d['id'], 'identifier': identifier(d, project), 'title': d['title'],
                           'filename': d['filename'], 'folder': d['folder'], 'revision': revision(project, d)} for d in project['documents']]}


def compare(old, new):
    before = {d['id']: d for d in old.get('documents', [])}; after = {d['id']: d for d in new['documents']}
    return {'added': [after[k]['title'] for k in after.keys()-before.keys()],
            'removed': [before[k]['title'] for k in before.keys()-after.keys()],
            'changed': [after[k]['title'] for k in after.keys() & before.keys() if after[k] != before[k]],
            'main_changed': old.get('main') != new['main'], 'references_changed': old.get('references') != new['references']}


def list_exports(store, project):
    folder = store.folder(project['id']) / 'exports'
    return sorted([json.loads(f.read_text('utf-8')) for f in folder.glob('*/manifest.json') if re.fullmatch('[a-f0-9]{32}', f.parent.name)],
                  key=lambda entry: entry['created_at'], reverse=True)


def save_export(store, project, data):
    parent = store.folder(project['id']) / 'exports'; parent.mkdir(exist_ok=True)
    previous = list_exports(store, project)
    mid = uuid4().hex; content = manifest(store, project)
    meta = {'id': mid, 'created_at': datetime.now(timezone.utc).isoformat(), 'sha256': sha256(data).hexdigest(),
            'size': len(data), 'manifest': content, 'changes': compare(previous[0]['manifest'] if previous else {}, content)}
    staging = parent / ('.pending-'+mid); staging.mkdir()
    # Incomplete staging directories are never visible in history.
    for name, value in [('Submission.zip', data), ('manifest.json', json.dumps(meta, ensure_ascii=False, indent=2).encode('utf-8'))]:
        with (staging/name).open('xb') as stream:
            stream.write(value); stream.flush(); os.fsync(stream.fileno())
    staging.rename(parent/mid)
    return meta


def read_export(store, project, eid):
    if not re.fullmatch('[a-f0-9]{32}', eid): raise ValueError('Некорректная версия комплекта.')
    folder = store.folder(project['id']) / 'exports' / eid
    try:
        meta = json.loads((folder/'manifest.json').read_text('utf-8'))
        data = (folder/'Submission.zip').read_bytes()
    except (OSError, ValueError) as exc: raise ValueError('Сохранённая версия недоступна.') from exc
    if sha256(data).hexdigest() != meta['sha256']: raise ValueError('Сохранённый ZIP изменён на диске. Скачивание остановлено.')
    return data
