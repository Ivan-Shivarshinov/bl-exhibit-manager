"""Exercise the frozen 0.8 app with synthetic OCR, library, volume and rollback fixtures."""
import argparse
from hashlib import file_digest
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.parse import urlencode
from urllib.request import urlopen
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from exhibit import __version__, volume_settings
from exhibit.samples import make_pdf
from tests.test_material_text import mixed_pdf
from verify_index_upgrade import running
from volume_checks import export as volume_export


PREVIOUS = '0.7.0'
PREVIOUS_HASHES = {
    'windows-x64': '0473709a29cfce2cca43389862241cd4219cf9ad38de34001f5b7d35a39d0c2e',
    'macos-x64': '54ff28bd4c187b2cce304d7cd8623425e042502853d73a8c81dd28bf3b79d07f',
    'macos-arm64': '0c7c4b5dbcaee72d99ab72d0c3fa1ad645f2fdfc5304a718ad00cedb9d603986',
}
CORRECTION = 'Corrected synthetic delivery date'
VOLUME_PHRASE = 'Translated synthetic agreement'
ENGLISH_TEXT = 'Shipment adjustment received and confirmed'
RUSSIAN_TEXT = 'Письмо о сроке отгрузки получено'


def wait(request, job):
    deadline = monotonic() + 90
    while monotonic() < deadline:
        state = request('/api/material-operations/' + job['id'])
        if state['state'] not in ('queued', 'running'):
            assert state['state'] == 'complete', state
            return state['result']
        sleep(.15)
    raise AssertionError('Text or library job timed out')


def operation(request, action, **arguments):
    return wait(request, request('/api/material-operations', {'action': action, 'arguments': arguments}))


def recognize(request, route, doc, language, expected):
    identity = {'did': doc['id'], 'part': 'original', 'source': doc['original']['sha256'], 'language': language}
    work = {'action': 'ocr_pages', 'pages': [1, 2], **identity}
    first = wait(request, request(route + '/text-job', work))
    assert first['embedded'] == 1 and first['completed'] == 2 and not first['failed'], first
    again = wait(request, request(route + '/text-job', work))
    assert again['reused'] == 1 and not again['failed'], again
    page = {key: identity[key] for key in ('part', 'source')} | {'page': 2}
    lookup = route + '/documents/' + doc['id'] + '/text-page'
    draft = request(lookup + '?' + urlencode(page))['record']
    assert draft['language'] == language and not draft['confirmed'], draft
    lines = [line['text'] for line in draft['lines']]
    selected = next(i for i, line in enumerate(lines) if expected.casefold() in line.casefold())
    return lookup, page, draft['revision'], lines, selected


def search_review(request, route):
    hit, = request(route + '/text-search', {'query': '"' + CORRECTION + '"'})['hits']
    assert (hit['page'], hit['method']) == (2, 'confirmed-ocr'), hit
    return hit


def check_restored(request, route):
    search_review(request, route)
    package, _ = volume_export(request, route)
    with ZipFile(BytesIO(package)) as archive:
        assert archive.read('Submission/Annex12.pdf') == mixed_pdf(ENGLISH_TEXT)
        assert 'Submission/Volumes/Volume map.json' in archive.namelist()


def exercise(request, route, doc, russian_pdf):
    lookup, page, revision, lines, selected = recognize(request, route, doc, 'eng', 'Shipment adjustment')
    lines[selected] = CORRECTION
    request(lookup + '/confirm', {**page, 'revision': revision, 'lines': lines})
    search_review(request, route)
    copy = request(route + '/documents/' + doc['id'] + '/searchable-copy?' + urlencode({k: page[k] for k in ('part', 'source')}))

    russian = request(route + '/upload?name=Annex13.pdf&mode=passthrough', raw=russian_pdf)['documents'][-1]
    request(route + '/documents/' + russian['id'], {'designation': 'Annex', 'number': 13})
    recognize(request, route, russian, 'rus', 'сроке отгрузки')

    library = request('/api/libraries', {'name': 'Synthetic case materials'})['id']
    pair = []
    for role, directory, contents in (('original', 'originals', make_pdf('Synthetic', [['Original synthetic agreement']])),
                                      ('translation', 'translations', make_pdf('Synthetic', [[VOLUME_PHRASE]]))):
        upload = request('/api/library-files?' + urlencode({'name': 'Contract.pdf', 'relative_path': directory + '/Contract.pdf'}), raw=contents)
        assert upload['part']['relative_path'] == directory + '/Contract.pdf'
        row = {'token': upload['token'], 'role': role, 'metadata': {'title': 'Synthetic agreement'}}
        if role == 'translation':
            row.update(pair=pair[0]['token'], confirmed=True)
        pair.append(row)
    plan = operation(request, 'upload_preview', lid=library, rows=pair)
    assert not plan['errors'], plan['errors']
    material, = operation(request, 'publish', token=plan['token'])['materials']
    plan = operation(request, 'save_preview', lid=library, pid=route.rsplit('/', 1)[1], rows=[{'document_id': doc['id']}])
    assert not plan['errors'], plan['errors']
    reviewed, = operation(request, 'publish', token=plan['token'])['materials']
    hit, = request('/api/libraries/' + library + '/text-search', {'query': '"' + CORRECTION + '"'})['hits']
    assert hit['version'] == reviewed['version_id'] and hit['method'] == 'confirmed-ocr', hit

    plan = operation(request, 'import_preview', pid=route.rsplit('/', 1)[1], rows=[{
        **material, 'number': 14, 'designation': 'Annex', 'filename': 'Annex14.pdf', 'folder': '', 'mode': 'prepare',
    }])
    assert not plan['errors'], plan['errors']
    prepared = operation(request, 'import_apply', token=plan['token'])['documents'][-1]
    request(route + '/documents/' + prepared['id'] + '/review/document', {})
    check_restored(request, route)

    hit, = request(route + '/text-search', {'scope': 'prepared', 'query': '"' + VOLUME_PHRASE + '"'})['hits']
    mapping = hit['volume']
    assert hit['part'] == 'translation' and mapping['page'] >= 1, hit

    other = '/api/projects/' + request('/api/projects', {'name': 'Synthetic searchable PDF reuse'})['id']
    request(other + '/upload?name=Searchable.pdf&mode=passthrough', raw=copy)
    hit, = request(other + '/text-search', {'query': '"' + CORRECTION + '"'})['hits']
    assert (hit['page'], hit['method']) == (2, 'embedded'), hit
    token = request(route + '/backup', {})['token']
    return request('/api/backups/' + token + '/download')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--old-package', type=Path)
    parser.add_argument('--exchange', type=Path)
    args = parser.parse_args()
    package = args.package.resolve()
    target = package.stem.split('-' + __version__ + '-')[-1]
    engine = Path(os.environ['EXHIBIT_TESSERACT'])
    version = subprocess.check_output([str(engine), '--version'], text=True).splitlines()[0]
    with TemporaryDirectory(prefix='bl-intake-upgrade-') as folder:
        root = Path(folder)
        data = root / 'data'
        old = args.old_package.resolve() if args.old_package else root / f'BLExhibitManager-{PREVIOUS}-{target}.zip'
        if not args.old_package:
            base = f'https://github.com/Ivan-Shivarshinov/bl-exhibit-manager/releases/download/v{PREVIOUS}/'
            with urlopen(base + old.name, timeout=90) as source, old.open('wb') as stream:
                shutil.copyfileobj(source, stream)
        with old.open('rb') as stream:
            old_sha = file_digest(stream, 'sha256').hexdigest()
        assert old_sha == PREVIOUS_HASHES[target], old_sha

        english, russian = mixed_pdf(ENGLISH_TEXT), mixed_pdf(RUSSIAN_TEXT)
        with running(old, root, data, 'old', previous_version=PREVIOUS) as request:
            assert request('/api/health')['version'] == PREVIOUS
            pid = request('/api/projects', {'name': 'Synthetic material intake upgrade'})['id']
            route = '/api/projects/' + pid
            doc = request(route + '/upload?name=Annex12.pdf&mode=passthrough', raw=english)['documents'][0]
            request(route + '/documents/' + doc['id'], {'designation': 'Annex', 'number': 12})
            current = request(route + '/volumes/settings', {**volume_settings.DEFAULT, 'enabled': True})
            assert current['schema'] == 4
            old_zip, _ = volume_export(request, route)
            eid = request(route + '/exports')[0]['id']

        project_file = data / pid / 'project.json'
        before = project_file.read_bytes()
        with running(package, root, data, 'new') as request:
            assert request('/api/health')['version'] == __version__
            assert request(route)['schema'] == 4 and project_file.read_bytes() == before
            assert set(request('/api/text/ocr')['languages']) == {'eng', 'rus'}
            checked = exercise(request, route, doc, russian)
            current = request(route)
            assert current['schema'] == 5 and request(route + '/exports/' + eid) == old_zip
            recovery = (data / current['schema_backup']).read_bytes()
            after = project_file.read_bytes()

        with running(old, root, data, 'old-rollback', previous_version=PREVIOUS) as request:
            assert 'Версия проекта' in request(route, expected=400)['detail']
            assert project_file.read_bytes() == after
            preview = request('/api/backups/preview', raw=recovery)
            restored = request('/api/backups/' + preview['token'] + '/restore', {'copy': True})
            assert restored['schema'] == 4
            assert request('/api/projects/' + restored['id'] + '/exports/' + eid) == old_zip

        empty = root / 'fresh-data'
        with running(package, root, empty, 'new-restore', extra_env={'EXHIBIT_TESSERACT': str(root / 'no-engine')}) as request:
            assert not request('/api/text/ocr')['available']
            preview = request('/api/backups/preview', raw=checked)
            restored = request('/api/backups/' + preview['token'] + '/restore', {'copy': False})
            assert restored['schema'] == 5
            check_restored(request, '/api/projects/' + restored['id'])

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'material-intake-result.json').write_text(json.dumps({
        'target': target, 'version': __version__, 'previous': PREVIOUS, 'previousPackageSha256': old_sha,
        'engine': version, 'models': ['eng', 'rus'], 'ocrQueueAndReview': True, 'libraryPairAndReuse': True,
        'exactCurrentVolume': True, 'searchableCopy': True, 'restoreWithoutEngine': True, 'rollback': True,
    }, indent=2), encoding='utf-8')
    if args.exchange:
        args.exchange.mkdir(parents=True, exist_ok=True)
        (args.exchange / 'material-intake-project.zip').write_bytes(checked)
    print(f'MATERIAL_INTAKE_OK {target} {version}')


if __name__ == '__main__':
    main()
