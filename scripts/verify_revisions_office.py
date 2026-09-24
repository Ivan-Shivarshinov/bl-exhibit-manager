"""HTTP workflow without Word pane, with a real PDF converter and synthetic inputs."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from pypdf import PdfReader
from exhibit import app, main_pdf, word
from exhibit.project import Store
from exhibit.samples import make_pdf
from tests.test_feedback import citation_docx


def verify(output):
    output.mkdir(parents=True, exist_ok=True)
    engine = main_pdf.available()
    assert engine['available'], engine
    with TemporaryDirectory() as temp, patch.object(app, 'store', Store(temp)), TestClient(app.app, base_url='http://localhost') as client:
        def post(path, body=None, raw=None):
            response = client.post(path, json=body if raw is None else None, content=raw, headers={'X-Exhibit-Local': '1'})
            assert response.status_code == 200, response.text
            return response.content if response.headers.get('content-type') == 'application/zip' else response.json()
        project = post('/api/projects', {'name': 'Revision training'})
        base = '/api/projects/' + project['id']
        source = make_pdf('Ready exhibit', [['Synthetic content']])
        project = post(base+'/upload?name=Annex%201.pdf&mode=passthrough', raw=source)
        did = project['documents'][0]['id']
        post(base+'/documents/'+did, {'prefix': '', 'designation': 'Annex', 'number': 1, 'folder': 'Exhibits/First'})
        texts = ['Annex 1, Training request, para. 3.', 'Annex 76, Video https://youtu.be/training?t=12', 'R-999, ordinary text.', 'Special document, p. 2.']
        original = citation_docx(texts)
        post(base+'/upload?kind=main&name=Main.docx', raw=original)
        project = post(base+'/scan')
        for ref in project['references']:
            if ref['mention'] == 'Annex 76': post(base+'/references', {'key': ref['key'], 'target': None, 'keep_original': True})
            elif ref['mention'] == 'R-999': post(base+'/references/exclude', {'key': ref['key']})
        post(base+'/references/add', {'fid': '4', 'paragraph': 0, 'mention': 'Special document', 'target': did})
        post(base+'/references/confirm')
        first = post(base+'/export')
        eid = client.get(base+'/exports').json()[0]['id']
        # Reload from disk as an existing project; no panel or HTTPS setup.
        app.store = Store(temp)
        project = post(base+'/upload?kind=main&name=Next.docx', raw=citation_docx(['New unrelated footnote.']+texts))
        assert not project['links_reviewed']
        project = post(base+'/scan')
        assert len(project['references']) == 3
        assert any(r.get('keep_original') for r in project['references'])
        assert any(r.get('custom') for r in project['references'])
        post(base+'/documents/'+did, {'folder': 'Exhibits/Second'})
        post(base+'/scan')
        post(base+'/references/confirm')
        second = post(base+'/export')
        assert client.get(base+'/exports/'+eid).content == first
        assert len(client.get(base+'/exports').json()) == 2
        for number, (data, folder) in enumerate(((first, 'First'), (second, 'Second')), 1):
            with ZipFile(BytesIO(data)) as archive:
                target = f'Exhibits/{folder}/Annex 1.pdf'
                assert archive.read('Submission/'+target) == source
                linked = word.package(archive.read('Submission/Main document.docx'))
                rels = word.xml(linked[word.RELS])
                assert target in [unquote(r.get('Target', '')) for r in rels], [r.get('Target') for r in rels]
                reader = PdfReader(BytesIO(archive.read('Submission/Main document.pdf')))
                targets = {a.get_object()['/A']['/F']['/UF'] for page in reader.pages for a in page.get('/Annots', []) if a.get_object().get('/A', {}).get('/S') == '/GoToR'}
                assert targets == {target}, targets
                text = word.text_of(word.xml(linked[word.FOOT]))
                assert 'https://youtu.be/training?t=12' in text and 'R-999, ordinary text.' in text
            (output/f'Edition-{number}.zip').write_bytes(data)
        report = {'engine': engine['label'], 'editions': 2, 'old_zip_unchanged': True, 'ready_pdf_unchanged': True, 'decisions_preserved': True, 'relative_docx_pdf_targets': True, 'panel_required': False}
        (output/'checks.json').write_text(json.dumps(report, indent=2), 'utf-8')
        print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', default='output/revisions-office')
    verify(Path(parser.parse_args().output).resolve())
