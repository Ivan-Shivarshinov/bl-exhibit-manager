from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree as E
from fastapi.testclient import TestClient
from exhibit import word, history, word_bridge
from exhibit.project import Store, revision
from exhibit.samples import make_pdf
from tests.test_feedback import citation_docx


def pack(parts):
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
        for name, value in parts.items(): archive.writestr(name, value)
    return out.getvalue()


class WordWorkflow(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name); self.p = self.store.create('Word test')
        self.store.upload(self.p, 'sample.pdf', make_pdf('Fictional document', [['Sample text']]))
        self.did = self.p['documents'][0]['id']
        self.store.update(self.p, self.did, {'title': 'Sample Code', 'prefix': 'RLA', 'number': 31})

    def main(self, texts):
        self.store.upload(self.p, 'Main.docx', citation_docx(texts), 'main')
        self.store.scan(self.p)
        self.store.confirm_links(self.p)

    def managed_docx(self, project=None, visible=None):
        text = 'RLA-31, Sample Code'
        self.main([text+', Article 421.'])
        address = word_bridge.link('https://localhost:8769', project or self.p['id'], self.did, text)
        data = word.add_links(self.store.source(self.p, self.p['main']), self.p['references'], {self.did:'sample.pdf'})
        parts = word.package(data); rels = word.xml(parts[word.RELS])
        for rel in rels:
            if rel.get('Type', '').endswith('/hyperlink'): rel.set('Target', address)
        parts[word.RELS] = E.tostring(rels)
        if visible:
            root = word.xml(parts[word.FOOT]); root.find('.//w:hyperlink/w:r/w:t',word.NS).text = visible
            parts[word.FOOT] = E.tostring(root)
        return pack(parts)

    def test_stable_identity_survives_rename_and_final_export_has_relative_link(self):
        data = self.managed_docx()
        self.store.update(self.p, self.did, {'title':'New Code', 'number':32})
        self.store.upload(self.p, 'New.docx', data, 'main')
        ref = self.p['references'][0]
        self.assertEqual(ref['target'], self.did)
        self.assertEqual(ref['link_text'], 'RLA-31, Sample Code')
        self.assertNotIn('Article',ref['link_text'])
        linked = self.store.linked_main(self.p)[0]
        rels = word.xml(word.package(linked)[word.RELS])
        used = [r.get('Target') for r in rels if r.get('Type','').endswith('/hyperlink')]
        self.assertIn('sample.pdf',used)
        self.assertTrue(all('localhost' not in x for x in used))

    def test_foreign_project_and_manual_text_not_silently_accepted(self):
        data = self.managed_docx(project='a'*32,visible='Edited title')
        self.store.upload(self.p, 'Foreign.docx', data, 'main')
        ref = self.p['references'][0]
        self.assertIsNone(ref['target']); self.assertTrue(ref['scope_review'])
        with self.assertRaises(ValueError): self.store.confirm_links(self.p)

    def test_unique_unchanged_paragraph_preserves_manual_range_after_move(self):
        self.main(['RLA-31, Sample Code, Article 421.','No citation.'])
        ref=self.p['references'][0]
        self.store.reference_range(self.p,ref['key'],0,len('RLA-31, Sample Code'))
        self.store.confirm_links(self.p)
        self.store.upload(self.p,'Next.docx',citation_docx(['No citation.','RLA-31, Sample Code, Article 421.']),'main')
        ref=self.p['references'][0]
        self.assertEqual(ref['footnote'],2);self.assertTrue(ref['scope_manual'])
        self.assertEqual(self.p['edition_report']['preserved'],1)
        self.assertFalse(self.p['links_reviewed'])
        self.store.scan(self.p)
        self.assertTrue(self.p['references'][0]['scope_manual'])

    def test_duplicate_or_changed_paragraph_requires_review(self):
        self.main(['RLA-31, Sample Code, Article 421.'])
        self.store.upload(self.p,'Next.docx',citation_docx(['RLA-31, Sample Code, Article 421.']*2),'main')
        self.assertEqual(self.p['edition_report']['preserved'],0)
        self.assertEqual(self.p['edition_report']['review'],2)

    def test_same_source_upload_does_not_discard_review(self):
        self.main(['RLA-31, Sample Code, Article 421.'])
        refs=deepcopy(self.p['references'])
        self.store.upload(self.p,'Rename.docx',self.store.source(self.p,self.p['main']),'main')
        self.assertTrue(self.p['links_reviewed']);self.assertEqual(self.p['references'],refs)

    def test_custom_mention_survives_unique_paragraph_move(self):
        self.store.upload(self.p,'Main.docx',citation_docx(['Special document, p. 2.','Nothing.']),'main')
        self.store.scan(self.p);self.store.add_reference(self.p,'1',0,'Special document')
        self.store.map_reference(self.p,self.p['references'][0]['key'],self.did)
        self.store.confirm_links(self.p)
        self.store.upload(self.p,'Next.docx',citation_docx(['Nothing.','Special document, p. 2.']),'main')
        self.assertEqual(self.p['references'][0]['footnote'],2)
        self.assertTrue(self.p['references'][0]['custom'])

    def test_short_title_updates_catalog_without_invalidating_pdf(self):
        doc=self.p['documents'][0];before=revision(self.p,doc)
        self.store.update(self.p,self.did,{'short_title':'Code'})
        self.assertEqual(revision(self.p,doc),before)
        self.assertEqual(word_bridge.catalog(self.p)['documents'][0]['short'],'RLA-31, Code')

    def test_immutable_history_and_tamper_detection(self):
        self.store.update(self.p,self.did,{'mode':'passthrough'})
        self.store.approve(self.p,self.did,'document')
        first=self.store.export(self.p);entries=history.list_exports(self.store,self.p)
        self.assertEqual(len(entries),1)
        self.store.update(self.p,self.did,{'title':'Renamed'})
        self.store.approve(self.p,self.did,'document');self.store.export(self.p)
        latest=history.list_exports(self.store,self.p)
        self.assertEqual(latest[0]['changes']['changed'],['Renamed'])
        self.assertEqual(history.read_export(self.store,self.p,entries[0]['id']),first)
        archive=Path(self.temp.name)/self.p['id']/'exports'/entries[0]['id']/'Submission.zip'
        archive.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'изменён'): history.read_export(self.store,self.p,entries[0]['id'])
        with self.assertRaises(ValueError): history.read_export(self.store,self.p,'../project.json')

    def test_failed_build_is_not_listed(self):
        with self.assertRaises(ValueError): self.store.export(self.p)
        self.assertEqual(history.list_exports(self.store,self.p),[])

    def test_bridge_does_not_open_cross_origin_access(self):
        from exhibit import app
        with patch.object(app,'store',self.store),TestClient(app.app,base_url='http://localhost') as client:
            r=client.get('/api/word/projects/'+self.p['id'],headers={'Origin':'https://evil.example'})
            self.assertEqual(r.status_code,403)
            self.assertEqual(client.get('/api/word/projects/'+self.p['id']).status_code,200)
            self.assertEqual(client.post('/api/projects/'+self.p['id']+'/citation-style',json={}).status_code,403)

    def test_settings_and_url_validation(self):
        with self.assertRaises(ValueError): word_bridge.check_settings({'separator':'<script>'})
        url=word_bridge.link('https://localhost:8769',self.p['id'],self.did,'RLA-31, Кодекс & Law')
        self.assertEqual(word_bridge.parse_link(url)['expected'],'RLA-31, Кодекс & Law')
        self.assertIsNone(word_bridge.parse_link(url.replace('localhost','evil.example')))

    def test_word_upload_rejects_concurrent_main_replacement(self):
        from exhibit import app
        self.main(['RLA-31, Sample Code, Article 421.'])
        previous = self.p['main']['sha256']
        self.store.upload(self.p, 'Next.docx', citation_docx(['RLA-31, Sample Code, Article 422.']), 'main')
        with patch.object(app,'store',self.store),TestClient(app.app,base_url='http://localhost') as client:
            response = client.post('/api/projects/'+self.p['id']+'/upload?kind=main&name=FromWord.docx',
                content=citation_docx(['RLA-31, Sample Code, Article 423.']),
                headers={'X-Exhibit-Local':'1','X-Exhibit-Main-Sha':previous})
            self.assertEqual(response.status_code,400)
            self.assertEqual(self.store.load(self.p['id'])['main']['sha256'],self.p['main']['sha256'])

    def test_managed_manual_mapping_survives_rescan(self):
        data=self.managed_docx(project='a'*32)
        self.store.upload(self.p,'Foreign.docx',data,'main')
        self.store.map_reference(self.p,self.p['references'][0]['key'],self.did)
        self.store.scan(self.p)
        self.assertEqual(self.p['references'][0]['target'],self.did)

    def test_no_file_and_exclusion_survive_unique_move_and_restart(self):
        texts = ['Annex 76, Video https://youtu.be/example?t=12', 'R-999, not an exhibit.', 'Special document, p. 2.']
        self.store.upload(self.p,'Main.docx',citation_docx(texts),'main')
        self.store.scan(self.p)
        video, false = self.p['references']
        self.store.map_reference(self.p,video['key'],None,keep_original=True)
        self.store.exclude_reference(self.p,false['key'])
        self.store.add_reference(self.p,'3',0,'Special document',target=self.did)
        self.store.confirm_links(self.p)
        self.store.upload(self.p,'Next.docx',citation_docx(['Nothing.']+texts),'main')
        self.assertEqual(len(self.p['excluded_references']),1)
        self.assertEqual(self.p['excluded_references'][0]['footnote'],3)
        self.assertTrue(next(r for r in self.p['references'] if r['mention']=='Annex 76')['keep_original'])
        self.assertEqual(next(r for r in self.p['references'] if r.get('custom'))['target'],self.did)
        self.assertFalse(self.p['links_reviewed'])
        self.p=self.store.load(self.p['id']); self.store.scan(self.p)
        self.assertEqual(len(self.p['references']),2)
        self.store.confirm_links(self.p)

    def test_duplicate_exclusion_and_no_file_need_new_decision(self):
        texts=['Annex 76, Video','R-999, not an exhibit.']
        self.store.upload(self.p,'Main.docx',citation_docx(texts),'main');self.store.scan(self.p)
        self.store.map_reference(self.p,self.p['references'][0]['key'],None,keep_original=True)
        self.store.exclude_reference(self.p,self.p['references'][1]['key']);self.store.confirm_links(self.p)
        self.store.upload(self.p,'Next.docx',citation_docx(texts*2),'main')
        self.assertFalse(self.p['excluded_references'])
        self.assertFalse(any(r.get('keep_original') for r in self.p['references']))
        with self.assertRaises(ValueError):self.store.confirm_links(self.p)

    def test_managed_no_file_survives_rescan(self):
        data=self.managed_docx()
        self.store.upload(self.p,'Managed.docx',data,'main')
        self.store.map_reference(self.p,self.p['references'][0]['key'],None,keep_original=True)
        self.store.scan(self.p);self.store.confirm_links(self.p)
        self.assertTrue(self.p['references'][0]['keep_original'])

    def test_ready_pdf_catalog_and_folder_history(self):
        self.p=self.store.use_originals(self.p,[self.did])
        self.assertTrue(word_bridge.catalog(self.p)['documents'][0]['ready'])
        first=self.store.export(self.p);entry=history.list_exports(self.store,self.p)[0]
        self.store.update(self.p,self.did,{'folder':'Exhibits/Authorities'})
        second=self.store.export(self.p)
        self.assertEqual(history.read_export(self.store,self.p,entry['id']),first)
        with ZipFile(BytesIO(second)) as z:self.assertIn('Submission/Exhibits/Authorities/sample.pdf',z.namelist())
