from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from io import BytesIO
from zipfile import ZipFile
import json

from exhibit import backup, schema
from exhibit.library import Library, check_version, metadata, translation_valid
from exhibit.project import Store, document_ready
from exhibit.samples import make_pdf


class LibraryTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = make_pdf('Original', [['Original paragraph']])
        cls.translation = make_pdf('Translation', [['Translated paragraph']])

    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name); self.library=Library(self.store)
        self.lib=self.library.create('Дело Example'); self.p=self.store.create('Источник')
        self.store.upload(self.p,'Original.pdf',self.original)
        self.d=self.p['documents'][0]
        self.store.update(self.p,self.d['id'],{'number':3,'designation':'Annex','title':'Материал Example','short_title':'Тест'})
        self.store.upload(self.p,'Translation.pdf',self.translation,'translation',self.d['id'])
        self.store.approve(self.p,self.d['id'],'translation')
        self.store.approve(self.p,self.d['id'],'document')

    def save(self, **extra):
        plan=self.library.save_preview(self.lib['id'],self.p['id'],[{'document_id':self.d['id'],**extra}])
        result=self.library.publish(plan['token'])['materials'][0]
        return plan,result

    def assignments(self, source, number=12, mode='prepare'):
        return {**source,'number':number,'designation':'Annex','filename':f'Annex {number}.pdf','folder':'Evidence','mode':mode}

    def test_valid_schema_and_reject_invalid_roles_identity_metadata_and_binding(self):
        _,source=self.save(); version=self.library.version(source['library_id'],source['material_id'],verify=True)
        self.assertEqual(check_version(version),version)
        for change in ({'schema':999},{'id':'../oops'},{'parts':{}},{'translation_binding':{}},{'metadata':{'title':''}}):
            with self.subTest(change=change),self.assertRaises(ValueError): check_version({**version,**change})
        with self.assertRaises(ValueError): metadata({'title':'Date','document_date':'2026-02-30'})

    def test_save_preserves_source_and_role_binding_without_project_references(self):
        before={n:p.read_bytes() for n,p in backup.inventory(self.store.folder(self.p['id']))}
        _,source=self.save(); v=self.library.version(source['library_id'],source['material_id'],verify=True)
        self.assertEqual(set(v['parts']),{'original','translation','ready'})
        self.assertEqual(v['recipe']['format'],self.store.public(self.p)['documents'][0]['effective_format'])
        self.assertNotIn('references',v); self.assertEqual(v['ready_identifier'],'Annex 3')
        self.assertEqual(before,{n:p.read_bytes() for n,p in backup.inventory(self.store.folder(self.p['id']))})

    def test_two_numbers_independent_files_ready_translation_and_export_without_library(self):
        _,source=self.save(); projects=[]
        for n in (3,12):
            p=self.store.create('Подача '+str(n)); plan=self.library.import_preview(p['id'],[self.assignments(source,n)])
            self.assertFalse(plan['errors']); self.library.import_apply(plan['token']); p=self.store.load(p['id']); d=p['documents'][0]
            self.assertEqual(self.store.source(p,d['original']),self.original)
            self.assertEqual(self.store.source(p,d['translation']),self.translation)
            self.assertTrue(translation_valid(d)); self.assertFalse(document_ready(p,d))
            self.assertEqual(d['number'],n); self.assertEqual(p['schema'],2)
            self.store.approve(p,d['id'],'document'); projects.append(p)
        before=json.loads((self.store.folder(projects[0]['id'])/'project.json').read_text('utf-8'))
        self.library.root.rename(self.store.root/'library-unavailable')
        for p in projects:
            with ZipFile(BytesIO(self.store.export(p))) as archive: self.assertIn(f"Submission/Evidence/Annex {p['documents'][0]['number']}.pdf",archive.namelist())
        self.assertEqual(before,self.store.load(projects[0]['id']))

    def test_passthrough_ready_file_is_identical_and_not_an_original(self):
        self.store.approve(self.p,self.d['id'],'remove_translation'); self.store.use_originals(self.p,[self.d['id']])
        _,source=self.save(); v=self.library.version(source['library_id'],source['material_id'])
        self.assertEqual(set(v['parts']),{'ready'})
        p=self.store.create('Finished'); preview=self.library.import_preview(p['id'],[self.assignments(source,12,'passthrough')])
        self.assertTrue(preview['warnings']); self.library.import_apply(preview['token']); p=self.store.load(p['id'])
        with ZipFile(BytesIO(self.store.export(p))) as z: self.assertEqual(z.read('Submission/Evidence/Annex 12.pdf'),self.original)
        rejected=self.library.import_preview(self.store.create('Prepare')['id'],[self.assignments(source)])
        self.assertTrue(rejected['errors'])

    def test_draft_requires_explicit_original_only_and_remains_in_source(self):
        self.store.upload(self.p,'Draft.pdf',self.translation,'translation',self.d['id'])
        plan=self.library.save_preview(self.lib['id'],self.p['id'],[{'document_id':self.d['id']}]); self.assertTrue(plan['errors'])
        with self.assertRaises(ValueError): self.library.publish(plan['token'])
        _,source=self.save(original_only=True); v=self.library.version(source['library_id'],source['material_id'])
        self.assertEqual(set(v['parts']),{'original'}); self.assertTrue(self.store.load(self.p['id'])['documents'][0]['translation'])

    def test_selection_change_revokes_translation_applicability(self):
        self.store.update(self.p,self.d['id'],{'selection':[{'page':1,'rect':[0,0,.5,.5]}]})
        self.assertFalse(translation_valid(self.d))
        plan=self.library.save_preview(self.lib['id'],self.p['id'],[{'document_id':self.d['id']}]);self.assertTrue(plan['errors'])

    def test_cancel_and_write_failure_do_not_publish_partial_material(self):
        plan=self.library.save_preview(self.lib['id'],self.p['id'],[{'document_id':self.d['id']}])
        with self.assertRaisesRegex(ValueError,'отмен'): self.library.publish(plan['token'],lambda:True)
        self.assertFalse(self.library.catalog(self.lib['id'])['items'])
        with patch('exhibit.library.shutil.copyfile',side_effect=OSError('disk full')),self.assertRaises(ValueError): self.library.publish(plan['token'])
        self.assertFalse(self.library.catalog(self.lib['id'])['items'])
        result=self.library.publish(plan['token']);self.assertEqual(len(result['materials']),1)

    def test_publish_retry_is_idempotent_and_other_window_detected(self):
        first=self.library.save_preview(self.lib['id'],self.p['id'],[{'document_id':self.d['id']}])
        second=self.library.save_preview(self.lib['id'],self.p['id'],[{'document_id':self.d['id']}])
        result=self.library.publish(first['token']);self.assertEqual(self.library.publish(first['token']),result)
        with self.assertRaisesRegex(ValueError,'изменилась'): self.library.publish(second['token'])
        self.assertEqual(self.library.catalog(self.lib['id'])['total'],1)

    def test_import_rejects_stale_project_conflicts_and_retries_safely(self):
        _,source=self.save(); p=self.store.create('Target'); row=self.assignments(source)
        plan=self.library.import_preview(p['id'],[row,row]);self.assertTrue(plan['errors'])
        with self.assertRaises(ValueError): self.library.import_apply(plan['token'])
        self.assertFalse(self.store.load(p['id'])['documents'])
        plan=self.library.import_preview(p['id'],[row]); changed=self.store.load(p['id']);changed['name']='Other tab';self.store.save(changed)
        with self.assertRaisesRegex(ValueError,'изменилась'): self.library.import_apply(plan['token'])
        plan=self.library.import_preview(p['id'],[row]);result=self.library.import_apply(plan['token']);self.assertEqual(self.library.import_apply(plan['token']),result)
        self.assertEqual(len(self.store.load(p['id'])['documents']),1)

    def test_failed_import_preserves_metadata_and_sources_before_schema_publication(self):
        _,source=self.save(); p=self.store.create('Target'); before=(self.store.folder(p['id'])/'project.json').read_bytes()
        plan=self.library.import_preview(p['id'],[self.assignments(source)])
        with patch.object(self.store,'save',side_effect=OSError('disk full')),self.assertRaises(ValueError): self.library.import_apply(plan['token'])
        self.assertEqual(before,(self.store.folder(p['id'])/'project.json').read_bytes())
        self.assertFalse(self.store.load(p['id'])['documents']);self.assertTrue(list((self.store.root/'backups').glob('before-upgrade-*.zip')))

    def test_missing_or_modified_version_stops_import_and_preview(self):
        _,source=self.save(); v=self.library.version(source['library_id'],source['material_id'])
        path=self.library.folder(self.lib['id'])/'blobs'/v['parts']['original']['blob'];path.write_bytes(b'damaged')
        p=self.store.create('Target');plan=self.library.import_preview(p['id'],[self.assignments(source)]);self.assertTrue(plan['errors'])
        with self.assertRaisesRegex(ValueError,'поврежден'): self.library.preview_page(self.lib['id'],source['material_id'],source['version_id'],'original',1)

    def test_unicode_catalog_pagination_filters_and_corruption_are_distinct(self):
        self.save(metadata={'category':'Evidence','tags':['Юникод'],'document_date':'2026-10-09'})
        self.assertEqual(self.library.catalog(self.lib['id'],query='материал')['total'],1)
        self.assertEqual(self.library.catalog(self.lib['id'],query='TRANSLATION')['total'],1)
        self.assertEqual(self.library.catalog(self.lib['id'],category='Wrong')['total'],0)
        self.assertEqual(self.library.catalog(self.lib['id'],tag='Юникод',translation='true')['total'],1)
        self.assertEqual(self.library.catalog(self.lib['id'],offset=1)['items'],[])
        other=self.library.create('Empty');self.assertEqual(self.library.catalog(other['id'])['total'],0)
        (self.library.folder(other['id'])/'library.json').write_text('broken','utf-8')
        with self.assertRaises(ValueError): self.library.catalog(other['id'])

    def test_lazy_migration_and_old_archive_preserve_bytes_and_future_refusal(self):
        raw=(self.store.folder(self.p['id'])/'project.json').read_bytes()
        self.assertEqual(self.store.load(self.p['id'])['schema'],1);self.assertEqual(raw,(self.store.folder(self.p['id'])/'project.json').read_bytes())
        archive=Path(self.temp.name)/'old.zip';backup.save_archive(self.store,self.p['id'],archive)
        target=Store(Path(self.temp.name)/'restored');restored=backup.restore_archive(target,archive)
        self.assertEqual(restored['schema'],1)
        self.assertEqual(raw,(target.folder(restored['id'])/'project.json').read_bytes())
        bad=deepcopy(self.p);bad['schema']=999;self.store.save(bad)
        future=(self.store.folder(bad['id'])/'project.json').read_bytes()
        with self.assertRaises(ValueError): self.store.load(bad['id'])
        self.assertEqual(future,(self.store.folder(bad['id'])/'project.json').read_bytes())
