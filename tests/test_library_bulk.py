from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from hashlib import sha256
from exhibit.library import atomic_json
from exhibit.samples import make_pdf
from exhibit import backup
from tests import test_library


class BulkTests(TestCase):
    setUpClass=classmethod(test_library.LibraryTests.setUpClass.__func__)
    setUp=test_library.LibraryTests.setUp
    save=test_library.LibraryTests.save
    assignments=test_library.LibraryTests.assignments

    def uploaded(self,name,data,role='original',**extra):
        folder=self.library.pending();part=self.library.stage_part(folder,name,data);atomic_json(folder/'part.json',part)
        return {'token':folder.name,'role':role,'metadata':{'title':Path(name).stem,'category':'Correspondence','tags':['key','Юникод'],'document_date':'2026-10-09','language':'Русский'},**extra}

    def test_explicit_pairs_draft_invalid_role_and_corrupt_pdf_fail_without_partial_library(self):
        o=self.uploaded('Original.pdf',self.original);t=self.uploaded('Translation.pdf',self.translation,'translation')
        plan=self.library.upload_preview(self.lib['id'],[o,t]);self.assertTrue(plan['errors'])
        with self.assertRaises(ValueError):self.library.publish(plan['token'])
        t.update(pair=o['token'],confirmed=False);self.assertTrue(self.library.upload_preview(self.lib['id'],[o,t])['errors'])
        t['confirmed']=True;plan=self.library.upload_preview(self.lib['id'],[o,t]);self.assertFalse(plan['errors'])
        material=self.library.publish(plan['token'])['materials'][0];v=self.library.version(material['library_id'],material['material_id'])
        self.assertEqual(set(v['parts']),{'original','translation'})
        with self.assertRaises(ValueError):self.library.stage_part(self.library.pending(),'bad.pdf',b'broken')
        with self.assertRaises(ValueError):self.library.upload_preview(self.lib['id'],[o,o])
        plan=self.library.upload_preview(self.lib['id'],[{**o,'role':''}]);self.assertTrue(plan['errors'])

    def test_100_pdf_atomic_upload_import_number_conflicts_and_export(self):
        rows=[self.uploaded(f'File {i}.pdf',make_pdf('File '+str(i),[['Distinct '+str(i)]]),'ready') for i in range(100)]
        plan=self.library.upload_preview(self.lib['id'],rows);self.assertFalse(plan['errors']);before=self.library.load(self.lib['id'])
        count=[0]
        def stopped():count[0]+=1;return count[0]>5
        with self.assertRaises(ValueError):self.library.publish(plan['token'],cancel=stopped)
        self.assertEqual(self.library.load(self.lib['id']),before)
        published=self.library.publish(plan['token'])['materials'];self.assertEqual(len(published),100)
        p=self.store.create('Mass target');imports=[self.assignments(m,n+1,'passthrough') for n,m in enumerate(published)]
        good=self.library.import_preview(p['id'],imports);self.assertFalse(good['errors'])
        count[0]=0
        with self.assertRaises(ValueError):self.library.import_apply(good['token'],cancel=stopped)
        self.assertFalse(self.store.load(p['id'])['documents'])
        self.library.import_apply(good['token']);p=self.store.load(p['id']);self.assertEqual(len(p['documents']),100)
        from zipfile import ZipFile
        from io import BytesIO
        with ZipFile(BytesIO(self.store.export(p))) as z:self.assertEqual(sum(n.endswith('.pdf') for n in z.namelist()),100)
        self.assertTrue(self.library.import_preview(p['id'],[imports[0]])['errors'])

    def test_exact_duplicates_content_not_names_and_metadata_filters(self):
        original=self.uploaded('One.pdf',self.original);renamed=self.uploaded('Other name.pdf',self.original)
        plan=self.library.upload_preview(self.lib['id'],[original,renamed]);self.assertTrue(plan['rows'][1]['duplicates'])
        with self.assertRaises(ValueError):self.library.publish(plan['token'])
        renamed['allow_duplicate']=True;self.library.publish(self.library.upload_preview(self.lib['id'],[original,renamed])['token'])
        changed=self.uploaded('One.pdf',self.translation);plan=self.library.upload_preview(self.lib['id'],[changed]);self.assertFalse(plan['rows'][0]['duplicates'])
        self.library.publish(plan['token'])
        self.assertEqual(self.library.catalog(self.lib['id'],category='Correspondence',tag='Юникод',language='Русский',translation='false',document_date='2026-10-09')['total'],3)
        self.assertEqual(self.library.catalog(self.lib['id'],translation='true')['total'],0)
        # Same original with a different confirmed translation is not an exact pair duplicate.
        t=self.uploaded('Translation.pdf',self.translation,'translation',pair=original['token'],confirmed=True)
        plan=self.library.upload_preview(self.lib['id'],[original,t]);self.assertFalse(plan['rows'][0]['duplicates'])

    def test_bulk_save_error_exclusion_revision_failure_after_copy_and_unicode_paths(self):
        self.store.upload(self.p,'bad draft.pdf',self.translation,'translation',self.d['id'])
        self.store.upload(self.p,'Finished.pdf',self.original,mode='passthrough');finished=self.p['documents'][-1]
        plan=self.library.save_preview(self.lib['id'],self.p['id'],[{'document_id':self.d['id']},{'document_id':finished['id']}]);self.assertTrue(plan['errors'])
        with self.assertRaises(ValueError):self.library.publish(plan['token'])
        plan=self.library.save_preview(self.lib['id'],self.p['id'],[{'document_id':finished['id']}])
        real=atomic_json
        def fail_index(path,value):
            if path.name=='library.json':raise OSError('disk full after copying')
            return real(path,value)
        with patch('exhibit.library.atomic_json',side_effect=fail_index),self.assertRaises(ValueError):self.library.publish(plan['token'])
        self.assertEqual(self.library.catalog(self.lib['id'])['total'],0)
        source=self.library.publish(plan['token'])['materials'][0];p=self.store.create('Unicode')
        a={**self.assignments(source,1,'passthrough'),'filename':'évidence.pdf'}
        b={**self.assignments(source,2,'passthrough'),'filename':'E\u0301vidence.PDF'}
        self.assertTrue(self.library.import_preview(p['id'],[a,b])['errors'])
        b={**b,'filename':'nested.pdf','folder':'Evidence/évidence.pdf'}
        self.assertTrue(self.library.import_preview(p['id'],[a,b])['errors'])

    def test_usage_project_copy_corruption_and_hide_preserve_files_and_versions(self):
        _,material=self.save();p=self.store.create('Usage target')
        self.library.import_apply(self.library.import_preview(p['id'],[self.assignments(material)])['token'])
        before={f.relative_to(self.store.root).as_posix():sha256(f.read_bytes()).hexdigest() for f in self.store.root.rglob('*') if f.is_file() and f.name not in ('library.json','plan.json')}
        revision=self.library.load(self.lib['id'])['revision'];self.library.hide(self.lib['id'],material['material_id'],True,revision)
        self.assertEqual(self.library.catalog(self.lib['id'])['total'],0);self.assertEqual(self.library.catalog(self.lib['id'],hidden='true')['total'],1)
        self.assertEqual(before,{f.relative_to(self.store.root).as_posix():sha256(f.read_bytes()).hexdigest() for f in self.store.root.rglob('*') if f.is_file() and f.name not in ('library.json','plan.json')})
        archive=self.store.root/'project.zip';backup.save_archive(self.store,p['id'],archive);copied=backup.restore_archive(self.store,archive,copy=True)
        usage=self.library.usage(self.lib['id'],material['material_id']);self.assertEqual(len(usage['items']),2)
        self.assertEqual({r['project_id'] for r in usage['items']},{p['id'],copied['id']})
        (self.store.folder(copied['id'])/'project.json').write_text('broken','utf-8')
        usage=self.library.usage(self.lib['id'],material['material_id']);self.assertEqual(len(usage['items']),1);self.assertEqual(len(usage['errors']),1)
        (self.store.folder(copied['id'])/'project.json').unlink();self.assertEqual(len(self.library.usage(self.lib['id'],material['material_id'])['errors']),1)
        self.library.hide(self.lib['id'],material['material_id'],False,revision+1);self.assertEqual(self.library.catalog(self.lib['id'])['total'],1)

