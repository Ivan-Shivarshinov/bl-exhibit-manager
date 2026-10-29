from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from pypdf import PdfReader
from exhibit import backup, history, index_documents, index_settings, submission_index as index
from exhibit.project import Store
from exhibit.samples import make_pdf
from tests.pdf_forms import form_pdf


class IndexTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=Store(self.temp.name);self.p=self.s.create('Synthetic submission')
        self.m=index.manager(self.s);self.addCleanup(self.m.close)
        for n in (10,2,1):
            self.s.upload(self.p,f'Annex {n}.pdf',make_pdf('Example',[[f'Page {n}']]),mode='passthrough')
            self.s.update(self.p,self.p['documents'][-1]['id'],{'number':n,'designation':'Annex'})
        self.p=index_settings.save(self.s,self.p,{**deepcopy(index_settings.DEFAULT),'enabled':True,'columns':['pages','path']})

    def test_rows_natural_project_folder_and_missing_identifier_do_not_mutate(self):
        before=deepcopy(self.p)
        result=self.m.preview(self.p['id'])
        self.assertEqual([r['identifier'] for r in result['rows']],['Annex 1','Annex 2','Annex 10'])
        self.assertEqual(self.s.load(self.p['id']),before)
        for order in ('project','folder'):
            self.p=index_settings.save(self.s,self.p,{**self.p['submission_index'],'order':order})
            result=self.m.preview(self.p['id'])
            self.assertEqual([r['identifier'] for r in result['rows']],['Annex 10','Annex 2','Annex 1'] if order=='project' else ['Annex 1','Annex 2','Annex 10'])
        self.p['documents'][0]['number']=None;self.s.save(self.p)
        self.assertEqual(next(r for r in self.m.preview(self.p['id'])['rows'] if r['id']==self.p['documents'][0]['id'])['identifier'],'')

    def test_snapshot_prepares_once_and_heading_change_reuses_exact_pdfs(self):
        with patch.object(self.s,'prepare',wraps=self.s.prepare) as prepare:
            first=self.m.preview(self.p['id']);self.assertEqual(prepare.call_count,3)
            self.s.export(self.p,first['preview_token']);self.assertEqual(prepare.call_count,3)
            self.p=index_settings.save(self.s,self.p,{**self.p['submission_index'],'title':'New heading'})
            second=self.m.preview(self.p['id']);self.s.export(self.p,second['preview_token'])
            self.assertEqual(prepare.call_count,3)
            self.assertNotEqual(first['preview_token'],second['preview_token'])

    def test_generator_is_stable_and_link_rectangles_do_not_overlap(self):
        preview=self.m.preview(self.p['id']);rows=preview['rows'];config=preview['settings']
        rows[0]['title']='Длинное название & symbols # % '*8
        for fmt in ('docx','pdf'):
            self.assertEqual(index_documents.generate(fmt,config,rows),index_documents.generate(fmt,config,rows))
        reader=PdfReader(BytesIO(index_documents.generate('pdf',config,rows)))
        for page in reader.pages:
            rects=[list(a.get_object()['/Rect']) for a in page.get('/Annots',[])]
            for i,a in enumerate(rects):
                for b in rects[i+1:]:self.assertFalse(a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3])

    def test_folder_sort_unicode_prefixes_and_equal_keys_are_stable(self):
        a,b,c=self.p['documents']
        self.s.update(self.p,a['id'],{'folder':'Папка 10','prefix':'R','number':10})
        self.s.update(self.p,b['id'],{'folder':'Папка 2','prefix':'R','number':2})
        self.s.update(self.p,c['id'],{'folder':'','prefix':'','number':1})
        self.p=index_settings.save(self.s,self.p,{**self.p['submission_index'],'order':'folder'})
        rows=self.m.preview(self.p['id'])['rows']
        self.assertEqual([r['id'] for r in rows],[c['id'],b['id'],a['id']])
        self.assertEqual(index.natural('CAFÉ 2'),index.natural('Cafe\u0301 2'))
        self.assertLess(index.natural('R-2'),index.natural('R-10'))

    def test_package_targets_special_paths_history_and_independent_restore(self):
        d=self.p['documents'][0]
        self.s.update(self.p,d['id'],{'title':'Название < & > full','folder':"Папка # 50%/O'Brien",'filename':'Annex # 10%.pdf'})
        original={k:sha256(f.read_bytes()).hexdigest() for k,f in backup.inventory(self.s.folder(self.p['id'])) if k.startswith('inputs/')}
        result=self.m.preview(self.p['id']);raw=self.s.export(self.p,result['preview_token'])
        index_documents.verify_package(raw,self.p['submission_index'],result['rows'])
        with ZipFile(BytesIO(raw)) as z:
            self.assertEqual(z.read("Submission/Папка # 50%/O'Brien/Annex # 10%.pdf"),self.s.source(self.p,d['original']))
            self.assertIn('Название < & > full', ''.join(__import__('xml.etree.ElementTree',fromlist=['']).fromstring(ZipFile(BytesIO(z.read('Submission/Exhibit index.docx'))).read('word/document.xml')).itertext()))
        first=history.list_exports(self.s,self.p)[0]
        self.p=index_settings.save(self.s,self.p,{**self.p['submission_index'],'title':'Second list'})
        result2=self.m.preview(self.p['id']);self.s.export(self.p,result2['preview_token'])
        latest=history.list_exports(self.s,self.p)[0]
        self.assertTrue(latest['changes']['index_changed']);self.assertFalse(latest['changes']['changed'])
        self.assertEqual(history.read_export(self.s,self.p,first['id']),raw)
        arc=Path(self.temp.name)/'project.zip';backup.save_archive(self.s,self.p['id'],arc)
        restored=backup.restore_archive(self.s,arc,copy=True)
        again=self.m.preview(restored['id']);self.s.export(restored,again['preview_token'])
        self.assertEqual(original,{k:sha256(f.read_bytes()).hexdigest() for k,f in backup.inventory(self.s.folder(self.p['id'])) if k.startswith('inputs/')})

    def test_ready_forms_and_signatures_are_byte_identical(self):
        for kind in ('form','signed'):
            self.s.upload(self.p,kind+'.pdf',form_pdf(kind),mode='passthrough')
        result=self.m.preview(self.p['id']);raw=self.s.export(self.p,result['preview_token'])
        with ZipFile(BytesIO(raw)) as z:
            for d in self.p['documents']:self.assertEqual(z.read('Submission/'+d['filename']),self.s.source(self.p,d['original']))

    def test_final_pages_include_translation_and_selections(self):
        p=self.s.create('Prepared');self.s.upload(p,'Annex 1.pdf',make_pdf('Original',[['One'],['Two'],['Three']]))
        d=p['documents'][0];self.s.update(p,d['id'],{'number':1});self.s.upload(p,'Translation.pdf',make_pdf('Translated',[['One'],['Two']]),'translation',d['id'])
        self.s.approve(p,d['id'],'translation');self.s.approve(p,d['id'],'document')
        p=index_settings.save(self.s,p,{**deepcopy(index_settings.DEFAULT),'enabled':True})
        self.assertEqual(self.m.preview(p['id'])['rows'][0]['pages'],5)

    def test_main_pdf_cache_is_independent_and_missing_converter_blocks_export(self):
        from tests import test_main_pdf as fixtures
        fixture=fixtures.MainPdf();fixture.setUp();self.addCleanup(fixture.doCleanups)
        store=fixture.store;p=index_settings.save(store,fixture.p,{**index_settings.DEFAULT,'enabled':True})
        manager=index.manager(store);self.addCleanup(manager.close)
        preview=manager.preview(p['id']);store.export(p,preview['preview_token'])
        self.assertEqual(fixture.office.call_count,1)
        p=index_settings.save(store,p,{**p['submission_index'],'title':'Another title'})
        preview=manager.preview(p['id']);store.export(p,preview['preview_token'])
        self.assertEqual(fixture.office.call_count,1)
        # No cached main PDF, no converter: a successful index preview is insufficient.
        with patch.object(store,'prepare_main_pdf',side_effect=ValueError('Конвертер основного PDF недоступен')),self.assertRaises(ValueError):
            store.export(p,preview['preview_token'])
        self.assertEqual(len(history.list_exports(store,p)),2)

    def test_stale_edit_during_preview_and_duplicate_requests(self):
        entered,release=Event(),Event();original=self.s.prepare
        def delayed(p,d):
            entered.set();release.wait(5);return original(p,d)
        with patch.object(self.s,'prepare',side_effect=delayed),ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.m.preview,self.p['id']);self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(ValueError,'уже'):self.m.preview(self.p['id'])
            p=self.s.load(self.p['id']);p['documents'][0]['title']='Other window';self.s.save(p);release.set()
            with self.assertRaisesRegex(ValueError,'Обновите'):future.result()
        self.assertFalse(self.m.plans)

    def test_stale_edit_during_export_never_publishes_and_cache_recovers(self):
        preview=self.m.preview(self.p['id'])
        entry=next(iter(self.m.plans[self.p['id']]['entries'].values()))
        Path(entry['file']).write_bytes(b'corrupt cache')
        with self.assertRaises(ValueError):self.s.export(self.p,preview['preview_token'])
        # A normal refresh repairs a missing/corrupt temporary file.
        preview=self.m.preview(self.p['id'])
        original=index_documents.generate
        def edit(fmt,config,rows):
            p=self.s.load(self.p['id']);p['documents'][0]['title']='Other window';self.s.save(p)
            return original(fmt,config,rows)
        with patch.object(index_documents,'generate',side_effect=edit),self.assertRaisesRegex(ValueError,'Обновите'):
            self.s.export(self.p,preview['preview_token'])
        self.assertFalse(history.list_exports(self.s,self.p))

    def test_old_token_and_generator_or_history_errors_never_publish(self):
        preview=self.m.preview(self.p['id']);token=preview['preview_token']
        with self.assertRaises(ValueError):self.s.export(self.p,'wrong')
        for target in ('generate','verify_package'):
            with patch.object(index_documents,target,side_effect=ValueError('failure')),self.assertRaises(ValueError):self.s.export(self.p,token)
            self.assertFalse(history.list_exports(self.s,self.p))
        with patch.object(Path,'rename',side_effect=OSError('disk full')),self.assertRaises(ValueError):self.s.export(self.p,token)
        self.assertFalse(list((self.s.folder(self.p['id'])/'exports').iterdir()))
        self.p['documents'][0]['title']='New title';self.s.save(self.p)
        with self.assertRaises(ValueError):self.s.export(self.p,token)

    def test_interruption_missing_source_and_disk_limit_clean_temporary_files(self):
        folders=[]
        def temporary(*args,**kwargs):
            value=TemporaryDirectory(*args,**kwargs);folders.append(Path(value.name));return value
        for error in (KeyboardInterrupt(),OSError('full')):
            with patch('exhibit.submission_index.TemporaryDirectory',side_effect=temporary),patch.object(self.s,'prepare',side_effect=error):
                with self.assertRaises((KeyboardInterrupt,ValueError)):self.m.preview(self.p['id'])
                self.assertFalse(self.m.plans)
        with patch('exhibit.submission_index.TemporaryDirectory',side_effect=temporary),patch.object(self.s,'prepare',return_value=b'broken PDF'),self.assertRaises(ValueError):self.m.preview(self.p['id'])
        self.assertTrue(folders);self.assertTrue(all(not f.exists() for f in folders))
        with patch.object(index,'MAX_BYTES',1),self.assertRaises(ValueError):self.m.preview(self.p['id'])
        (self.s.folder(self.p['id'])/'inputs'/self.p['documents'][0]['original']['blob']).unlink()
        with self.assertRaises(ValueError):self.m.preview(self.p['id'])

    def test_name_conflicts_and_selected_formats(self):
        self.s.update(self.p,self.p['documents'][0]['id'],{'filename':'EXHIBIT INDEX.pdf'})
        with self.assertRaisesRegex(ValueError,'конфликт'):self.m.preview(self.p['id'])
        self.p=index_settings.save(self.s,self.p,{**self.p['submission_index'],'formats':['docx']})
        preview=self.m.preview(self.p['id']);self.s.export(self.p,preview['preview_token'])
        self.s.update(self.p,self.p['documents'][0]['id'],{'filename':'File.pdf','folder':'Exhibit index.docx'})
        with self.assertRaisesRegex(ValueError,'конфликт'):self.m.preview(self.p['id'])

    def test_unsupported_glyph_and_oversized_row_fail_before_history(self):
        for title in ('Unsupported \U0001f9ea','Long title '*1500):
            rows=[{'id':'x','identifier':'Annex 1','title':title,'path':'File.pdf','pages':1}]
            with self.assertRaises(ValueError):index_documents.generate('pdf',self.p['submission_index'],rows)
