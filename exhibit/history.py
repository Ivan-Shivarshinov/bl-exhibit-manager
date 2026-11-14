"""Immutable exports: publish one complete snapshot directory by atomic rename."""
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import json
import os
import re
import shutil
from io import BytesIO
from zipfile import ZipFile


def manifest(store, project):
    from .project import identifier, revision, digest
    from .index_settings import settings
    config = settings(project)
    profile=project.get('applied_profile')
    return {'submission_index': config if config['enabled'] else None, 'main': (project.get('main') or {}).get('sha256'),
            'profile':{k:profile[k] for k in ('profile_id','version_id','hash','name')} if profile else None,
            'volume_settings':None,
            'assignment_operation':project.get('assignment_operations',[])[-1]['id'] if project.get('assignment_operations') else None,
            'references': digest(project['references']),
            'documents': [{'id': d['id'], 'identifier': identifier(d, project), 'title': d['title'],
                           'filename': d['filename'], 'folder': d['folder'], 'revision': revision(project, d)} for d in project['documents']]}


def compare(old, new):
    before = {d['id']: d for d in old.get('documents', [])}; after = {d['id']: d for d in new['documents']}
    return {'added': [after[k]['title'] for k in after.keys()-before.keys()],
            'removed': [before[k]['title'] for k in before.keys()-after.keys()],
            'changed': [after[k]['title'] for k in after.keys() & before.keys() if after[k] != before[k]],
            'main_changed': old.get('main') != new['main'], 'references_changed': old.get('references') != new['references'],
            'index_changed': old.get('submission_index') != new.get('submission_index') or old.get('index_files',[]) != new.get('index_files',[]),
            'volumes_changed':old.get('volume_map_sha256')!=new.get('volume_map_sha256') or old.get('volume_files',[])!=new.get('volume_files',[]),
            'profile_changed':old.get('profile')!=new.get('profile'),
            'assignments_changed':old.get('assignment_operation')!=new.get('assignment_operation')}


def list_exports(store, project):
    folder = store.folder(project['id']) / 'exports'
    return sorted([json.loads(f.read_text('utf-8')) for f in folder.glob('*/manifest.json') if re.fullmatch('[a-f0-9]{32}', f.parent.name)],
                  key=lambda entry: entry['created_at'], reverse=True)


def save_export(store, project, data):
    parent = store.folder(project['id']) / 'exports'; parent.mkdir(exist_ok=True)
    previous = list_exports(store, project)
    mid = uuid4().hex; content = manifest(store, project)
    if content['submission_index']:
        with ZipFile(BytesIO(data)) as archive:
            content['index_files'] = [{'path':n,'kind':'submission_index','sha256':sha256(archive.read(n)).hexdigest()}
                                     for n in archive.namelist() if n in ('Submission/Exhibit index.docx','Submission/Exhibit index.pdf')]
    with ZipFile(BytesIO(data)) as archive:
        map_path='Submission/Volumes/Volume map.json'
        if map_path in archive.namelist():
            raw=archive.read(map_path);mapping=json.loads(raw)
            content['volume_map_sha256']=sha256(raw).hexdigest()
            content['volume_settings']=mapping['settings']
            content['volume_files']=[{'path':'Submission/Volumes/'+v['file'],'pages':v['pages'],
                                      'sha256':sha256(archive.read('Submission/Volumes/'+v['file'])).hexdigest()} for v in mapping['volumes']]
    created=datetime.now(timezone.utc)
    if previous:
        last=datetime.fromisoformat(previous[0]['created_at'])
        if created<=last:created=last+timedelta(microseconds=1)
    meta = {'id': mid, 'created_at': created.isoformat(), 'sha256': sha256(data).hexdigest(),
            'size': len(data), 'manifest': content, 'changes': compare(previous[0]['manifest'] if previous else {}, content)}
    staging = parent / ('.pending-'+mid); staging.mkdir()
    # Incomplete staging directories are never visible in history.
    try:
        for name, value in [('Submission.zip', data), ('manifest.json', json.dumps(meta, ensure_ascii=False, indent=2).encode('utf-8'))]:
            with (staging/name).open('xb') as stream:
                stream.write(value); stream.flush(); os.fsync(stream.fileno())
        staging.rename(parent/mid)
    finally:
        if staging.exists(): shutil.rmtree(staging)
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
