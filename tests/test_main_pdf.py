import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch, Mock
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED
from reportlab.pdfgen import canvas
from pypdf import PdfReader
from exhibit import main_pdf, word, pdf
from exhibit.project import Store
from exhibit.samples import demo_project, files


def office_fixture(source, target, directory, engine):
    """Office boundary substitute. Link postprocessing and storage still run normally."""
    parts = word.package(source.read_bytes())
    urls = [r.get('Target') for r in word.xml(parts[word.RELS]) if r.get('Target', '').startswith('https://exhibit.invalid/')]
    out = BytesIO(); c = canvas.Canvas(out, invariant=1)
    c.drawString(40, 800, 'Converted document')
    for index, url in enumerate(urls): c.linkURL(url, (40, 750-index*25, 300, 770-index*25))
    c.linkURL('https://example.org/source', (40, 30, 300, 50))
    c.save(); target.write_bytes(out.getvalue())


class MainPdf(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name); self.p = demo_project(self.store)
        self.store.upload(self.p, 'RLA-99.pdf', files()['RLA-99.pdf'])
        self.store.update(self.p, self.p['documents'][-1]['id'], {'prefix':'RLA','number':99,'folder':'RLA'})
        for d in self.p['documents']:
            if d['translation']: self.store.approve(self.p,d['id'],'translation')
            self.store.approve(self.p,d['id'],'document')
        self.store.scan(self.p); self.store.confirm_links(self.p)
        engine = patch.object(main_pdf, 'available', return_value={'available':True,'engine':'word','label':'Fixture Office'})
        engine.start(); self.addCleanup(engine.stop)
        office = patch.object(main_pdf, 'office_export', side_effect=office_fixture)
        self.office = office.start(); self.addCleanup(office.stop)

    def test_relative_links_preserve_every_repeat_and_external_url(self):
        data = self.store.prepare_main_pdf(self.p)
        page = PdfReader(BytesIO(data)).pages[0]
        actions = [x.get_object()['/A'] for x in page['/Annots']]
        remote = [x for x in actions if x['/S']=='/GoToR']
        self.assertEqual(len(remote),6)
        self.assertEqual(sum(x['/F']['/UF']=='RLA/RLA-31.pdf' for x in remote),3)
        self.assertIn('Other documents/Statement of Claim.pdf',[x['/F']['/UF'] for x in remote])
        self.assertTrue(all(x['/D'][0]==0 and ':' not in x['/F']['/UF'] for x in remote))
        self.assertIn('https://example.org/source',[x.get('/URI') for x in actions])
        self.assertNotIn(b'exhibit.invalid',data)
        for item in page['/Annots']:
            annotation=item.get_object()
            if annotation['/A']['/S']=='/GoToR':
                self.assertEqual(list(annotation['/Border']),[0,0,0])
                self.assertNotIn('/C',annotation);self.assertNotIn('/BS',annotation)
        self.assertIn('Converted document',page.extract_text())

    def test_missing_even_one_repeated_link_blocks_pdf_and_zip(self):
        def drop_one(source,target,directory,engine):
            parts=word.package(source.read_bytes()); rels=word.xml(parts[word.RELS])
            markers={r.get('Target'):'RLA/RLA-31.pdf' for r in rels if r.get('Target','').startswith('https://exhibit.invalid/')}
            out=BytesIO();c=canvas.Canvas(out);c.drawString(20,800,'Missing one repeated link')
            for index,uri in enumerate(list(markers)[1:]):c.linkURL(uri,(20,20+index*30,200,40+index*30))
            c.save();target.write_bytes(out.getvalue())
        self.office.side_effect=drop_one
        with self.assertRaisesRegex(ValueError,'потеряны ссылки'):self.store.export(self.p)
        self.assertFalse(self.store.main_pdf_status(self.p)['ready'])

    def test_cache_reload_tamper_and_explicit_refresh(self):
        first=self.store.prepare_main_pdf(self.p)
        self.assertEqual(self.store.prepare_main_pdf(self.p),first)
        self.assertEqual(self.office.call_count,1)
        loaded=Store(self.temp.name)
        self.assertEqual(loaded.read_main_pdf(loaded.load(self.p['id'])),first)
        status=loaded.main_pdf_status(self.p)
        (loaded.folder(self.p['id'])/'main-pdf'/(status['key']+'.pdf')).write_bytes(b'broken')
        self.assertFalse(loaded.main_pdf_status(self.p)['ready'])
        loaded.prepare_main_pdf(self.p);self.assertEqual(self.office.call_count,2)
        loaded.prepare_main_pdf(self.p,refresh=True);self.assertEqual(self.office.call_count,3)

    def test_path_and_source_changes_cannot_reuse_old_pdf(self):
        self.store.prepare_main_pdf(self.p)
        d=self.p['documents'][0]
        self.store.update(self.p,d['id'],{'folder':'Новая папка с пробелами'})
        self.assertFalse(self.store.main_pdf_status(self.p)['ready'])
        with self.assertRaises(ValueError):self.store.read_main_pdf(self.p)
        self.store.approve(self.p,d['id'],'document');self.store.scan(self.p);self.store.confirm_links(self.p)
        result=self.store.prepare_main_pdf(self.p)
        paths=[a.get_object()['/A'].get('/F',{}).get('/UF') for a in PdfReader(BytesIO(result)).pages[0]['/Annots']]
        self.assertIn('Новая папка с пробелами/RLA-31.pdf',paths)
        # A new edition must change actual content, not rely on ZIP timestamps.
        # Identical re-uploads now intentionally preserve the reviewed state.
        from lxml import etree as E
        parts=word.package(self.store.source(self.p,self.p['main']))
        root=word.xml(parts['word/document.xml'])
        first=root.find('.//w:t',word.NS);first.text=(first.text or '')+' — revised edition'
        parts['word/document.xml']=E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
        changed=BytesIO()
        with ZipFile(changed,'w',ZIP_DEFLATED) as archive:
            for name,data in parts.items():archive.writestr(name,data)
        self.store.upload(self.p,'Replaced.docx',changed.getvalue(),kind='main')
        self.assertFalse(self.store.main_pdf_status(self.p)['ready'])

    def test_converter_unavailable_has_actionable_error_without_partial_zip(self):
        with patch.object(main_pdf,'available',return_value={'available':False,'message':'Установите локальный конвертер'}):
            with self.assertRaisesRegex(ValueError,'локальный конвертер'):self.store.export(self.p)
        self.assertFalse(self.store.main_pdf_status(self.p)['ready'])
        self.office.side_effect=ValueError('Конвертация PDF превысила 120 секунд')
        with self.assertRaisesRegex(ValueError,'120 секунд'):self.store.export(self.p)

    def test_complete_zip_after_relocation_and_docx_preservation(self):
        source=self.store.source(self.p,self.p['main'])
        with ZipFile(BytesIO(self.store.export(self.p))) as z:
            self.assertEqual(len(z.namelist()),7)
            docx=z.read('Submission/Main document.docx')
            old,new=word.package(source),word.package(docx)
            for name in old:
                if name not in (word.FOOT,word.RELS):self.assertEqual(old[name],new[name])
            self.assertNotIn(b'exhibit.invalid',new[word.RELS])
            moved=Path(self.temp.name)/'Перенесённый комплект'
            z.extractall(moved)
        reader=PdfReader(moved/'Submission/Main document.pdf')
        for item in reader.pages[0]['/Annots']:
            a=item.get_object()['/A']
            if a['/S']=='/GoToR':self.assertTrue((moved/'Submission'/a['/F']['/UF']).is_file())

    def test_main_pdf_name_and_ancestor_are_reserved(self):
        d=self.p['documents'][0]
        self.store.update(self.p,d['id'],{'folder':'','filename':'MAIN DOCUMENT.PDF'})
        self.assertIn('duplicate_path',[x['code'] for x in self.store.validate(self.p)])
        self.store.update(self.p,d['id'],{'folder':'Main document.pdf','filename':'child.pdf'})
        self.assertIn('path_hierarchy',[x['code'] for x in self.store.validate(self.p)])

    def test_http_preview_prepare_download_and_error(self):
        from fastapi.testclient import TestClient
        import exhibit.app as module
        with patch.object(module,'store',self.store),TestClient(module.app,base_url='http://127.0.0.1') as client:
            path=f'/api/projects/{self.p["id"]}/main-pdf'
            self.assertFalse(client.get(path+'/status').json()['ready'])
            self.assertEqual(client.get(path+'/download').status_code,400)
            self.assertEqual(client.post(path+'/prepare').status_code,403)
            response=client.post(path+'/prepare',headers={'X-Exhibit-Local':'1'})
            self.assertTrue(response.json()['ready'])
            self.assertTrue(client.get(path+'/preview').content.startswith(b'\x89PNG'))
            self.assertEqual(client.get(path+'/preview?page=2').status_code,400)
            self.assertTrue(client.get(path+'/download').content.startswith(b'%PDF'))


class OfficeProcess(unittest.TestCase):
    def test_timeout_terminates_only_owned_process_group(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);proc=Mock(pid=4321)
            proc.wait.side_effect=[subprocess.TimeoutExpired('office',120),0]
            with patch.object(main_pdf.sys,'platform','linux'), patch.object(main_pdf.subprocess,'Popen',return_value=proc) as start, patch.object(main_pdf.os,'killpg',create=True) as kill, patch('signal.SIGKILL',9,create=True):
                with self.assertRaisesRegex(ValueError,'120 секунд'):
                    main_pdf.office_export(directory/'Input.docx',directory/'Input.pdf',directory,{'engine':'libreoffice','executable':'/office/soffice'})
                kill.assert_called_once()
                self.assertEqual(kill.call_args.args[0],4321)
                self.assertTrue(start.call_args.kwargs['start_new_session'])
                self.assertIn('-env:UserInstallation='+ (directory/'office-profile').as_uri(),start.call_args.args[0])

    def test_success_exit_without_pdf_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);proc=Mock();proc.wait.return_value=0
            with patch.object(main_pdf.subprocess,'Popen',return_value=proc):
                with self.assertRaisesRegex(ValueError,'Не удалось создать'):
                    main_pdf.office_export(directory/'Input.docx',directory/'Input.pdf',directory,{'engine':'libreoffice','executable':'soffice'})

    def test_macos_discovers_standard_libreoffice_app(self):
        with patch.object(main_pdf.sys,'platform','darwin'),patch.object(main_pdf.shutil,'which',return_value=None),patch.object(main_pdf.Path,'is_file',lambda path:str(path).replace('\\','/')=='/Applications/LibreOffice.app/Contents/MacOS/soffice'):
            self.assertEqual(main_pdf.available()['engine'],'libreoffice')
