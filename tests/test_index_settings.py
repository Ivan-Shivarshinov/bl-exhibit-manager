from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from exhibit import backup, index_settings, schema
from exhibit.project import Store
from tests import test_library_versions as fixtures


class SettingsTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name); self.p = self.store.create('Index example')

    def test_defaults_are_independent_and_read_does_not_write_or_upgrade(self):
        path = self.store.folder(self.p['id'])/'project.json'; before = path.read_bytes()
        first = index_settings.settings(self.store.load(self.p['id']))
        first['columns'].append('path')
        self.assertEqual(index_settings.settings(self.p), index_settings.DEFAULT)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.store.load(self.p['id'])['schema'], 1)

    def test_strict_settings_reject_unsupported_types_and_text(self):
        invalid = [{'unknown':1}, {'enabled':1}, {'language':[]}, {'language':'fr'}, {'title':''},
                   {'title':'a'*201}, {'title':'line\nline'}, {'title':'\x00'}, {'order':[]},
                   {'formats':'pdf'}, {'formats':['pdf','pdf']}, {'formats':['html']},
                   {'columns':['title']}, {'columns':[True]}, {'enabled':True,'formats':[]}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                index_settings.validate({**deepcopy(index_settings.DEFAULT), **changes})
        for language in ('ru','en'):
            for formats in (['pdf'],['docx'],['docx','pdf']):
                v = {**deepcopy(index_settings.DEFAULT),'enabled':True,'language':language,'formats':formats}
                self.assertEqual(index_settings.validate(v),v)

    def test_upgrade_roundtrip_and_old_app_refusal_for_both_legacy_schemas(self):
        for version in (1,2):
            p = self.store.create('Legacy'); p['schema']=version; self.store.save(p)
            original=(self.store.folder(p['id'])/'project.json').read_bytes()
            config={**deepcopy(index_settings.DEFAULT),'enabled':True,'language':'en','title':'Custom title','order':'folder','columns':['path','pages']}
            updated=index_settings.save(self.store,p,config)
            self.assertEqual(updated['schema'],3)
            before=backup.inspect_archive(self.store.root/updated['schema_backup'])['project']
            self.assertEqual(before,p)
            self.assertEqual(backup.json_data(original),p)
            with patch.object(schema,'CURRENT',2),self.assertRaises(ValueError): self.store.load(p['id'])
            self.assertEqual(self.store.load(p['id']),updated)
            archive=self.store.root/('schema3-'+str(version)+'.zip')
            backup.save_archive(self.store,p['id'],archive)
            restored=backup.restore_archive(self.store,archive,copy=True)
            self.assertEqual(restored['submission_index'],config)
            updated=index_settings.save(self.store,updated,{**config,'enabled':False})
            self.assertEqual(updated['schema'],3)
            self.assertEqual(self.store.load(p['id'])['submission_index']['title'],'Custom title')

    def test_backup_verification_and_metadata_write_failure_preserve_original(self):
        path=self.store.folder(self.p['id'])/'project.json'; original=path.read_bytes()
        for where in ('copy','verify','save'):
            target = patch.object(backup,'save_archive',side_effect=OSError('full')) if where=='copy' else patch.object(schema,'CURRENT',3)
            failure = patch.object(backup,'inspect_archive',side_effect=ValueError('bad archive')) if where=='verify' else patch.object(self.store,'save',side_effect=OSError('denied'))
            with target, failure, self.assertRaises((ValueError,OSError)):
                index_settings.save(self.store,self.p,index_settings.DEFAULT)
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(self.store.load(self.p['id'])['schema'],1)

    def test_archive_validation_rejects_settings_in_wrong_schema_and_malformed_schema3(self):
        for config, version in [(index_settings.DEFAULT,1), ({**index_settings.DEFAULT,'order':'random'},3), (None,3)]:
            p=deepcopy(self.p);p['schema']=version;p['submission_index']=config
            with self.assertRaises(ValueError): backup.check_project(p,{},lambda _:b'')


class LibraryIndexSettingsTests(TestCase):
    setUpClass = classmethod(fixtures.VersionTests.setUpClass.__func__)
    setUp = fixtures.VersionTests.setUp
    save = fixtures.VersionTests.save
    assignments = fixtures.VersionTests.assignments
    imported = fixtures.VersionTests.imported
    v2 = fixtures.VersionTests.v2

    def test_library_import_update_rollback_and_restore_keep_index_settings(self):
        for original_schema in (1,2):
            if original_schema == 2: self.setUp()
            _,v1=self.save(); other=self.imported(v1,30+original_schema)
            other_before=(self.store.folder(other['id'])/'project.json').read_bytes()
            p=self.store.create('Index target');p['schema']=original_schema;self.store.save(p)
            config={**deepcopy(index_settings.DEFAULT),'enabled':True,'title':'Independent index'}
            p=index_settings.save(self.store,p,config)
            plan=self.library.import_preview(p['id'],[self.assignments(v1,12)])
            self.library.import_apply(plan['token']);p=self.store.load(p['id'])
            v2=self.v2(v1)
            for target in (v2,v1):
                update=self.library.update_preview(p['id'],p['documents'][0]['id'],target['version_id'])
                self.library.update_apply(update['token']);p=self.store.load(p['id'])
                self.assertEqual(p['submission_index'],config)
            archive=self.store.root/('independent-'+str(original_schema)+'.zip')
            backup.save_archive(self.store,p['id'],archive)
            fresh=Store(self.store.root/('fresh'+str(original_schema)))
            restored=backup.restore_archive(fresh,archive,copy=True)
            self.assertEqual(restored['submission_index'],config)
            self.assertEqual(other_before,(self.store.folder(other['id'])/'project.json').read_bytes())
