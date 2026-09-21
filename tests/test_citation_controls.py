from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from lxml import etree as E
from exhibit import word
from exhibit.project import Store
from exhibit.samples import make_pdf
from tests.test_feedback import citation_docx
from tests.test_reference_options import pack


class CitationControls(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = Store(self.temp.name)
        self.p = self.s.create('Citation controls')
        for name in ('Annex 39', 'Annex 40', 'Procedural document'):
            self.s.upload(self.p, name+'.pdf', make_pdf('Training', [['Fictional material']]), mode='passthrough')

    def scan(self, text):
        self.data = citation_docx([text])
        self.s.upload(self.p, 'Main.docx', self.data, 'main')
        self.s.scan(self.p)

    def test_case_numbers_stay_in_titles_and_existing_project_rescans(self):
        text = 'Annex 39, Fictional ruling, Case No. A41-12345/24, p. 1; Annex 40, Other ruling, Case No. A41-12345/24.'
        self.scan(text)
        refs = self.p['references']
        self.assertEqual([r['mention'] for r in refs], ['Annex 39','Annex 40'])
        self.assertEqual(refs[0]['link_text'], text.split(', p.')[0])
        self.assertTrue(refs[1]['link_text'].endswith('A41-12345/24'))
        start = text.index('A41-12345')
        # Model an old saved decision on the former false match.
        self.p['references'].append({'key':f'1:0:{start}:{start+9}', 'fid':'1', 'paragraph':0,
            'footnote':1, 'start':start, 'end':start+9, 'mention':'A41-12345', 'manual':True,
            'keep_original':True, 'target':None, 'candidates':[]})
        self.s.save(self.p)
        self.p = self.s.load(self.p['id'])
        self.s.scan(self.p)
        self.assertEqual(len(self.p['references']),2)
        self.s.reference_range(self.p,refs[0]['key'],0,text.index(', p.'))
        self.s.confirm_links(self.p)
        self.assertEqual(self.s.source(self.p,self.p['main']),self.data)
        linked = word.xml(word.package(self.s.linked_main(self.p)[0])[word.FOOT])
        self.assertEqual(len(linked.findall('.//w:hyperlink',word.NS)),2)

    def test_case_context_and_known_identifiers(self):
        text = 'Case No. AB-123; case number C-45; дело № А41-12345/24; RLA-12; R-19; XY7-8; Annex 39.'
        self.scan(text)
        self.assertEqual([r['mention'] for r in self.p['references']], ['RLA-12','R-19','XY7-8','Annex 39'])
        docs=[{'id':'known','identifier':'A41-12345','title':'Fictional','aliases':[]}]
        self.assertEqual(word.scan(citation_docx(['Case No. A41-12345/24']),docs)['references'],[])

    def test_exclusion_frees_range_survives_reload_and_can_restore(self):
        text = 'Annex 39, Training title with XYZ-123, p. 1; Annex 40, Other title.'
        self.scan(text)
        false = self.p['references'][1]
        self.s.exclude_reference(self.p,false['key'])
        self.assertEqual(len(self.p['references']),2)
        self.assertEqual(self.p['references'][0]['link_text'],text.split(', p.')[0])
        self.s.confirm_links(self.p)
        self.p = self.s.load(self.p['id']); self.s.scan(self.p)
        self.assertEqual(len(self.p['references']),2)
        self.assertEqual(len(self.p['excluded_references']),1)
        self.s.exclude_reference(self.p,false['key'],restore=True)
        self.assertEqual(len(self.p['references']),3)
        self.assertFalse(self.p['excluded_references'])
        self.s.scan(self.p)
        self.assertEqual(len(self.p['references']),3)
        self.assertEqual(self.s.source(self.p,self.p['main']),self.data)

    def test_exclusion_is_per_occurrence_and_new_docx_clears_it(self):
        self.scan('XYZ-123; XYZ-123.')
        self.s.exclude_reference(self.p,self.p['references'][0]['key'])
        self.s.scan(self.p)
        self.assertEqual(len(self.p['references']),1)
        self.s.upload(self.p,'New.docx',self.data,'main'); self.s.scan(self.p)
        self.assertEqual(len(self.p['references']),2)
        self.assertEqual(self.p['excluded_references'],[])

    def test_selected_occurrence_with_unicode_and_mapping_survives_scan(self):
        text = '😀 Training Submission, para. 1; Training Submission, para. 2; Annex 39, Title, p. 3.'
        self.scan(text)
        mention='Training Submission';start=text.rindex(mention)
        target=self.p['documents'][2]['id']
        self.s.add_reference(self.p,'1',0,mention,start,start+len(mention),target)
        self.s.scan(self.p)
        manual=[r for r in self.p['references'] if r.get('custom')]
        self.assertEqual(len(manual),1)
        self.assertEqual(manual[0]['start'],start)
        self.assertEqual(manual[0]['target'],target)
        self.assertEqual(manual[0]['link_text'],mention)
        self.s.confirm_links(self.p)
        root = word.xml(word.package(self.s.linked_main(self.p)[0])[word.FOOT])
        self.assertEqual([word.text_of(h) for h in root.findall('.//w:hyperlink',word.NS)], [mention,'Annex 39, Title'])
        snapshot=deepcopy(self.p)
        with self.assertRaises(ValueError):
            self.s.add_reference(self.p,'1',0,mention,start+1,start+len(mention),target)
        self.assertEqual(self.p,snapshot)

    def test_only_designation_is_bold_and_title_runs_keep_source_style(self):
        self.scan('Annex 39, Italic title, p. 2; Training Submission, para. 3.')
        parts=word.package(self.data);root,rows=word.paragraphs(parts);p=rows[0][-1];p[:]=[]
        for text,italic in [('Annex 39, ',False),('Italic title',True),(', p. 2; Training Submission, para. 3.',False)]:
            r=E.SubElement(p,f'{{{word.W}}}r');pr=E.SubElement(r,f'{{{word.W}}}rPr')
            E.SubElement(pr,f'{{{word.W}}}b',{f'{{{word.W}}}val':'0'})
            if italic:E.SubElement(pr,f'{{{word.W}}}i')
            E.SubElement(r,f'{{{word.W}}}t').text=text
        parts[word.FOOT]=E.tostring(root)
        self.s.upload(self.p,'Main.docx',pack(parts),'main');self.s.scan(self.p)
        text=self.p['footnotes'][0]['text'];start=text.index('Training Submission')
        self.s.add_reference(self.p,'1',0,'Training Submission',start,start+19,self.p['documents'][2]['id'])
        linked=word.xml(word.package(self.s.linked_main(self.p)[0])[word.FOOT])
        runs=linked.findall('.//w:hyperlink/w:r',word.NS)
        bold=[word.text_of(r) for r in runs if r.find('w:rPr/w:b',word.NS).get(f'{{{word.W}}}val')=='1']
        self.assertEqual(bold,['Annex 39'])
        italic=[word.text_of(r) for r in runs if r.find('w:rPr/w:i',word.NS) is not None]
        self.assertEqual(italic,['Italic title'])
        for r in runs:
            self.assertEqual(r.find('w:rPr/w:color',word.NS).get(f'{{{word.W}}}val'),'000000')
            self.assertEqual(r.find('w:rPr/w:u',word.NS).get(f'{{{word.W}}}val'),'none')

    def test_api_exclusion_and_selected_add(self):
        import exhibit.app as module
        self.scan('XYZ-123; Training Submission, para. 1.')
        key=self.p['references'][0]['key'];pid=self.p['id']
        with patch.object(module,'store',self.s), TestClient(module.app,base_url='http://127.0.0.1') as client:
            headers={'X-Exhibit-Local':'1'}
            response=client.post(f'/api/projects/{pid}/references/exclude',json={'key':key},headers=headers)
            self.assertEqual(response.status_code,200)
            response=client.post(f'/api/projects/{pid}/references/add',json={'fid':'1','paragraph':0,'mention':'Training Submission','start':9,'end':28,'target':self.p['documents'][2]['id']},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['references'][0]['target'],self.p['documents'][2]['id'])
