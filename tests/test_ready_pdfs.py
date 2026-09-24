from copy import deepcopy
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from exhibit import pdf, word
from exhibit.project import Store
from tests.test_feedback import citation_docx


def finished_pdf():
    out = BytesIO()
    c = canvas.Canvas(out, pagesize=(595, 1200))
    c.setFillColorRGB(0, 0, 0)
    c.rect(0, 1140, 595, 60, fill=1, stroke=0)
    c.drawString(30, 1100, 'Fictional finished exhibit')
    c.showPage()
    c.drawString(30, 100, 'Second page must be preserved')
    c.save()
    return out.getvalue()


class ReadyPdfs(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = Store(self.temp.name)
        self.p = self.s.create('Finished exhibits')
        self.data = finished_pdf()

    def test_eighty_finished_pdfs_export_without_stamping_or_individual_review(self):
        with patch.object(pdf, 'prepare_part', side_effect=AssertionError('Must not process finished PDF')):
            for n in range(1, 81):
                self.s.upload(self.p, f'Annex {n}.pdf', self.data, mode='passthrough')
            self.p = self.s.load(self.p['id'])
            self.assertTrue(all(d['ready'] for d in self.s.public(self.p)['documents']))
            self.assertEqual(self.s.validate(self.p), [])
            with ZipFile(BytesIO(self.s.export(self.p))) as z:
                self.assertEqual(len(z.namelist()), 80)
                for n in range(1, 81):
                    self.assertEqual(z.read(f'Submission/Annex {n}.pdf'), self.data)

    def test_existing_documents_switch_atomically_and_preserve_paths_selections_and_links(self):
        for n in (1, 2):
            self.s.upload(self.p, f'Annex {n}.pdf', self.data)
        first, second = self.p['documents']
        self.s.update(self.p, first['id'], {'selection': [{'page': 2}], 'folder': 'Evidence'})
        self.s.upload(self.p, 'Main.docx', citation_docx(['Annex 1, First title, para. 2; Annex 2, Second title, p. 3.']), 'main')
        self.s.scan(self.p)
        self.assertTrue(all(r['target'] for r in self.p['references']))
        self.s.confirm_links(self.p)
        linked_before = word.package(self.s.linked_main(self.p)[0])
        before = deepcopy(self.p)
        self.p = self.s.use_originals(self.p, [first['id'], second['id']])
        self.assertTrue(self.p['links_reviewed'])
        self.assertEqual(self.p['references'], before['references'])
        self.assertEqual(word.package(self.s.linked_main(self.p)[0]), linked_before)
        for old, new in zip(before['documents'], self.p['documents']):
            for key in old.keys() - {'mode', 'approved'}:
                self.assertEqual(new[key], old[key])
            self.assertEqual(self.s.prepare(self.p, new), self.data)
        self.assertEqual(self.s.validate(self.p), [])

    def test_attached_translation_or_missing_source_does_not_partially_switch_batch(self):
        for name in ('Annex 1.pdf', 'Annex 2.pdf'):
            self.s.upload(self.p, name, self.data)
        ids = [d['id'] for d in self.p['documents']]
        self.s.upload(self.p, 'Translation.pdf', self.data, 'translation', ids[1])
        before = deepcopy(self.p)
        with self.assertRaisesRegex(ValueError, 'прикреплён перевод'):
            self.s.use_originals(self.p, ids)
        self.assertEqual(self.p, before)
        self.assertEqual(self.s.load(self.p['id']), before)
        self.s.approve(self.p, ids[1], 'remove_translation')
        before = deepcopy(self.p)
        source = self.p['documents'][1]['original']
        (self.s.folder(self.p['id'])/'inputs'/source['blob']).write_bytes(b'changed externally')
        with self.assertRaisesRegex(ValueError, 'изменён вне'):
            self.s.use_originals(self.p, ids)
        self.assertEqual(self.s.load(self.p['id']), before)

    def test_names_integrity_and_unmatched_citations_still_block_export(self):
        for _ in range(2):
            self.s.upload(self.p, 'Annex 1.pdf', self.data, mode='passthrough')
        self.assertIn('duplicate_path', {i['code'] for i in self.s.validate(self.p)})
        with self.assertRaisesRegex(ValueError, 'заблокирована'):
            self.s.export(self.p)
        self.s.update(self.p, self.p['documents'][1]['id'], {'filename': 'Annex 2.pdf'})
        self.s.upload(self.p, 'Main.docx', citation_docx(['Annex 99, Missing file.']), 'main')
        self.s.scan(self.p)
        self.assertIn('unmatched', {i['code'] for i in self.s.validate(self.p)})
        with self.assertRaisesRegex(ValueError, 'несопоставленные'):
            self.s.confirm_links(self.p)
        source = self.p['documents'][0]['original']
        (self.s.folder(self.p['id'])/'inputs'/source['blob']).write_bytes(b'changed')
        self.assertIn('source', {i['code'] for i in self.s.validate(self.p)})

    def test_return_to_preparation_requires_review_and_detects_stamp_collision(self):
        self.s.upload(self.p, 'Annex 1.pdf', self.data, mode='passthrough')
        doc = self.p['documents'][0]
        self.s.update(self.p, doc['id'], {'mode': 'prepare', 'number': 1})
        self.assertFalse(self.s.public(self.p)['documents'][0]['ready'])
        with self.assertRaisesRegex(ValueError, 'пересекает'):
            self.s.approve(self.p, doc['id'], 'document')
        self.p = self.s.use_originals(self.p, [doc['id']])
        self.s.update(self.p, doc['id'], {'folder': 'Other', 'filename': 'Finished.pdf'})
        self.s.style(self.p, {'font': 'Helvetica', 'size': 12, 'margin': 24})
        self.assertEqual(self.s.validate(self.p), [])
        self.assertEqual(self.s.prepare(self.p, self.p['documents'][0]), self.data)

    def test_http_ready_upload_batch_replacement_and_input_validation(self):
        import exhibit.app as module
        headers = {'X-Exhibit-Local': '1'}
        url = f'/api/projects/{self.p["id"]}'
        with patch.object(module, 'store', self.s), TestClient(module.app, base_url='http://127.0.0.1') as client:
            response = client.post(url+'/upload', params={'name':'Annex 1.pdf', 'mode':'passthrough'}, content=self.data, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            doc = response.json()['documents'][0]
            self.assertTrue(doc['ready'])
            replacement = client.post(url+'/upload', params={'name':'Replacement.pdf', 'did':doc['id']}, content=self.data, headers=headers)
            self.assertTrue(replacement.json()['documents'][0]['ready'])
            self.assertEqual(client.post(url+'/ready-pdfs', json={'document_ids':[doc['id']]}).status_code, 403)
            for ids in ([], None, ['unknown'], [doc['id'],doc['id']]):
                self.assertEqual(client.post(url+'/ready-pdfs', json={'document_ids':ids}, headers=headers).status_code, 400)
            self.assertEqual(client.post(url+'/ready-pdfs', json={'document_ids':[doc['id']]}, headers=headers).status_code, 200)
            invalid = client.post(url+'/upload', params={'name':'bad.pdf', 'mode':'unknown'}, content=self.data, headers=headers)
            self.assertEqual(invalid.status_code, 400)
            invalid = client.post(url+'/upload', params={'name':'bad.docx', 'kind':'main', 'mode':'passthrough'}, content=b'bad', headers=headers)
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(len(self.s.load(self.p['id'])['documents']), 1)
