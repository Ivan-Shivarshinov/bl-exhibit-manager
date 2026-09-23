from copy import deepcopy
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import unquote
from zipfile import ZipFile

from exhibit import word
from exhibit.project import Store, revision
from exhibit.samples import make_pdf
from tests.test_feedback import citation_docx


class SubmissionFolders(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=Store(self.temp.name);self.p=self.s.create('Folders')
        self.data=make_pdf('Fictional',[['Training PDF']])
        for n in (1,2):self.s.upload(self.p,f'Annex {n}.pdf',self.data,mode='passthrough')
        self.ids=[d['id'] for d in self.p['documents']]
        self.s.upload(self.p,'Main.docx',citation_docx(['Annex 1, Training one, p. 1; Annex 2, Training two, para. 3.']),'main')
        self.s.scan(self.p);self.s.confirm_links(self.p)

    def move(self, destinations):
        request={'action':'folders','destinations':destinations}
        _,report=self.s.batch_plan(self.p,request)
        self.assertEqual(report['conflicts'],[])
        self.p=self.s.batch_apply(self.p,request,report['token'])
        return report

    def test_nested_paths_preserve_mappings_and_invalidate_main_pdf_cache(self):
        before=deepcopy(self.p['references']);oldkey=self.s.main_pdf_key(self.p)[0]
        destinations={self.ids[0]:"Exhibits/Respondent's Evidence",self.ids[1]:'Exhibits/Правовые материалы'}
        self.move(destinations)
        self.assertEqual(self.p['references'],before)
        self.assertTrue(self.p['scanned']);self.assertTrue(self.p['links_reviewed'])
        self.assertNotEqual(self.s.main_pdf_key(self.p)[0],oldkey)
        self.p=self.s.load(self.p['id'])
        parts=word.package(self.s.linked_main(self.p)[0]);rels=word.xml(parts[word.RELS])
        targets={unquote(r.get('Target','')) for r in rels if r.get('TargetMode')=='External'}
        expected={f'{destinations[d["id"]]}/{d["filename"]}' for d in self.p['documents']}
        self.assertTrue(expected<=targets)
        # Native Office conversion is covered by the separate integration script.
        with patch.object(self.s,'prepare_main_pdf',return_value=self.data):
            with ZipFile(BytesIO(self.s.export(self.p))) as z:
                self.assertEqual(set(z.namelist()),{'Submission/Main document.docx','Submission/Main document.pdf',*('Submission/'+f for f in expected)})
                for f in expected:self.assertEqual(z.read('Submission/'+f),self.data)
        self.move({self.ids[0]:''})
        self.assertTrue(self.p['links_reviewed'])
        self.assertEqual(self.p['documents'][0]['folder'],'')

    def test_folder_move_keeps_prepared_review_when_format_does_not_change(self):
        self.s.update(self.p,self.ids[0],{'mode':'prepare','number':1,'designation':'Annex','prefix':''})
        self.s.approve(self.p,self.ids[0],'document')
        before=self.s.prepare(self.p,self.p['documents'][0])
        report=self.move({self.ids[0]:'Exhibits/Evidence'})
        self.assertFalse(report['changes'][0]['requires_review'])
        self.assertEqual(self.p['documents'][0]['approved'],revision(self.p,self.p['documents'][0]))
        # PDF contents and appearance remain identical (PDF serializer IDs may vary).
        from pypdf import PdfReader
        after=self.s.prepare(self.p,self.p['documents'][0])
        self.assertEqual(PdfReader(BytesIO(before)).pages[0].extract_text(),PdfReader(BytesIO(after)).pages[0].extract_text())

    def test_inherited_format_or_designation_change_requires_review(self):
        self.s.update(self.p,self.ids[0],{'mode':'prepare','number':1,'prefix':'','format_overrides':{}})
        self.s.approve(self.p,self.ids[0],'document')
        self.p['scanned']=True;self.p['links_reviewed']=True
        self.p['group_styles']={'exhibits':{'designation':'Annex','size':12}}
        self.s.save(self.p)
        report=self.move({self.ids[0]:'Exhibits'})
        self.assertTrue(report['links_need_review'])
        self.assertTrue(report['changes'][0]['requires_review'])

    def test_conflicting_paths_and_invalid_folders_are_atomic(self):
        self.s.update(self.p,self.ids[0],{'filename':'same.pdf','folder':'A'})
        self.s.update(self.p,self.ids[1],{'filename':'SAME.pdf','folder':'B'})
        before=self.s.load(self.p['id'])
        request={'action':'folders','destinations':{did:'Exhibits' for did in self.ids}}
        _,report=self.s.batch_plan(self.p,request)
        self.assertIn('duplicate_path',[x['code'] for x in report['conflicts']])
        with self.assertRaises(ValueError):self.s.batch_apply(self.p,request,report['token'])
        for folder in ('../escape','C:/secret','Exhibits/CON','Exhibits//A'):
            with self.assertRaises(ValueError):self.s.batch_plan(self.p,{'action':'folders','destinations':{self.ids[0]:'Good',self.ids[1]:folder}})
        self.assertEqual(self.s.load(self.p['id']),before)

    def test_conflict_with_main_document_and_stale_preview(self):
        request={'action':'folders','destinations':{self.ids[0]:'Main document.pdf/Nested'}}
        _,report=self.s.batch_plan(self.p,request)
        self.assertIn('path_hierarchy',[x['code'] for x in report['conflicts']])
        request={'action':'folders','destinations':{self.ids[0]:'Exhibits'}}
        _,report=self.s.batch_plan(self.p,request)
        self.s.update(self.p,self.ids[1],{'title':'Changed'})
        with self.assertRaisesRegex(ValueError,'изменились'):self.s.batch_apply(self.p,request,report['token'])
