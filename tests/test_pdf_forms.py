from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile
from PIL import Image
from pypdf import PdfReader, PdfWriter
from fastapi.testclient import TestClient

from exhibit import pdf, backup, word
from exhibit.project import Store, DEFAULT_STYLE
from tests.pdf_forms import form_pdf
from tests.test_feedback import citation_docx


class PdfForms(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'data'); self.p=self.store.create('Synthetic forms')

    def test_readability_does_not_grant_transformation(self):
        for kind in ('form','widget','unsigned','signed','perms'):
            with self.subTest(kind=kind):
                data=form_pdf(kind)
                self.assertEqual(pdf.inspect_pdf(data,allow_forms=True)['pages'],2)
                with self.assertRaisesRegex(ValueError,'Добавить готовые PDF'): pdf.inspect_pdf(data)
                with self.assertRaisesRegex(ValueError,'Добавить готовые PDF'):
                    pdf.prepare_part(data,[{'page':1}],'','',DEFAULT_STYLE)
        self.assertEqual(pdf.inspect_pdf(form_pdf('empty'))['pages'],2)

    def test_passthrough_bytes_paths_links_archive_restore_and_guard(self):
        for kind in ('form','widget','unsigned','signed'):
            data=form_pdf(kind); p=self.store.create(kind)
            self.store.upload(p,'Annex 65.pdf',data,mode='passthrough'); doc=p['documents'][0]
            self.store.update(p,doc['id'],{'filename':'Renamed.pdf','folder':'Evidence/Subfolder'})
            self.store.upload(p,'Main.docx',citation_docx(['Annex 65, Artificial form.']),'main')
            self.store.scan(p); self.store.confirm_links(p)
            self.assertEqual(p['references'][0]['target'],doc['id'])
            self.assertIn(b'Evidence/Subfolder/Renamed.pdf',word.package(self.store.linked_main(p)[0])[word.RELS])
            # Actual Office conversion is verified separately; this test verifies byte preservation.
            with patch.object(self.store,'prepare_main_pdf',return_value=form_pdf('empty')):
                export=self.store.export(p)
            with ZipFile(BytesIO(export)) as z:
                self.assertEqual(z.read('Submission/Evidence/Subfolder/Renamed.pdf'),data)
            before=deepcopy(p); disk=(self.store.folder(p['id'])/'project.json').read_bytes()
            with self.assertRaisesRegex(ValueError,'Добавить готовые PDF'):
                self.store.update(p,doc['id'],{'mode':'prepare','title':'Must not persist'})
            self.assertEqual(p,before); self.assertEqual((self.store.folder(p['id'])/'project.json').read_bytes(),disk)
            # Guard a malicious/direct mode override too.
            with self.assertRaisesRegex(ValueError,'Добавить готовые PDF'):
                self.store.prepare(p,{**doc,'mode':'prepare'})
            saved=backup.save_archive(self.store,p['id'],Path(self.temp.name)/(kind+'.zip'))
            restored=backup.restore_archive(self.store,saved,copy=True)
            self.assertEqual(self.store.prepare(restored,restored['documents'][0]),data)
            with patch.object(self.store,'prepare_main_pdf',return_value=form_pdf('empty')):
                with ZipFile(BytesIO(self.store.export(restored))) as z:
                    self.assertEqual(z.read('Submission/Evidence/Subfolder/Renamed.pdf'),data)

    def test_widget_appearance_in_preview_and_unchanged_bytes(self):
        for kind in ('form','widget','unsigned','signed'):
            data=form_pdf(kind); image=Image.open(BytesIO(pdf.render_png(data,2,1))).convert('RGB')
            self.assertGreater(sum(g>r+40 and g>b+40 for r,g,b in image.get_flattened_data()),300)

    def test_invalid_and_encrypted_pdf_still_rejected_and_api_atomic(self):
        with self.assertRaises(ValueError): pdf.inspect_pdf(b'not pdf',allow_forms=True)
        w=PdfWriter(); w.add_blank_page(width=595,height=842); w.encrypt('secret'); out=BytesIO(); w.write(out)
        with self.assertRaisesRegex(ValueError,'Защищённые'): pdf.inspect_pdf(out.getvalue(),allow_forms=True)
        from exhibit import app as api
        data=form_pdf(); self.store.upload(self.p,'Annex 65.pdf',data,mode='passthrough')
        pid=self.p['id']; did=self.p['documents'][0]['id']
        with patch.object(api,'store',self.store),TestClient(api.app,base_url='http://127.0.0.1') as client:
            before=self.store.load(pid)
            response=client.post(f'/api/projects/{pid}/documents/{did}',json={'mode':'prepare'},headers={'X-Exhibit-Local':'1'})
            self.assertEqual(response.status_code,400); self.assertIn('Добавить готовые PDF',response.text)
            self.assertEqual(before,self.store.load(pid))

    def test_limits_and_archive_cannot_grant_prepare_permission(self):
        for size in ((9,842),(595,14401)):
            w=PdfWriter();w.add_blank_page(width=size[0],height=size[1]);out=BytesIO();w.write(out)
            with self.assertRaisesRegex(ValueError,'Размер страницы'):pdf.inspect_pdf(out.getvalue(),allow_forms=True)
        with self.assertRaisesRegex(ValueError,'50 МБ'):
            self.store.upload(self.p,'Large.pdf',b'x'*50_000_001,mode='passthrough')
        data=form_pdf('widget');self.store.upload(self.p,'Ready.pdf',data,mode='passthrough')
        self.p['documents'][0]['mode']='prepare';self.store.save(self.p)
        dest=Path(self.temp.name)/'not-published.zip'
        with self.assertRaisesRegex(ValueError,'Добавить готовые PDF'):
            backup.save_archive(self.store,self.p['id'],dest)
        self.assertFalse(dest.exists())
