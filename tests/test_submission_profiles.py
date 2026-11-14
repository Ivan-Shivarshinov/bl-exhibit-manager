from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase
import json
from exhibit.project import Store,effective_format
from exhibit.submission_profiles import Profiles,capture
from tests.test_volumes import sample

OPTIONS={'reset_overrides':False,'apply_paths':False,'apply_prefix':False}


class ProfileTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=Store(self.temp.name);self.p=self.s.create('Private project name not a profile')
        self.s.upload(self.p,'Original.pdf',sample(),mode='passthrough')
        self.did=self.p['documents'][0]['id']
        self.s.update(self.p,self.did,{'designation':'Annex','number':12,'prefix':'','filename':'Manual.pdf'})
        self.profiles=Profiles(self.s)

    def test_immutable_versions_and_private_free_portable_package(self):
        rules=capture(self.p);package=self.profiles.create('Reusable rules',rules)
        old=deepcopy(package);new=self.profiles.create('Reusable rules v2',{**rules,'format':{**rules['format'],'size':12}},package['profile_id'])
        self.assertEqual(new['versions'][0],old['versions'][0]);self.assertEqual(len(new['versions']),2)
        encoded=json.dumps(new,ensure_ascii=False).encode()
        self.assertNotIn(self.p['name'].encode(),encoded);self.assertNotIn(self.did.encode(),encoded)
        with TemporaryDirectory() as other:
            imported=Profiles(Store(other)).import_package(encoded);self.assertEqual(imported,new)
            self.assertEqual(Profiles(Store(other)).import_package(json.dumps(old).encode()),new)

    def test_preview_does_not_write_defaults_keep_local_annex_manual_name(self):
        package=self.profiles.create('Exhibit profile',capture(self.p,naming='identifier'))
        version=package['versions'][0];file=self.s.folder(self.p['id'])/'project.json';before=file.read_bytes()
        args=(self.p['id'],package['profile_id'],version['version_id'],OPTIONS)
        _,preview=self.profiles.preview(*args);self.assertEqual(before,file.read_bytes())
        self.assertFalse((Path(self.temp.name)/'backups').exists())
        p=self.profiles.apply(*args,preview['token'])
        self.assertEqual(effective_format(p,p['documents'][0])[0]['designation'],'Annex')
        self.assertEqual(p['documents'][0]['filename'],'Manual.pdf');self.assertEqual(p['documents'][0]['number'],12)
        self.assertEqual(p['applied_profile'],version)
        later=self.profiles.create('New',{**capture(self.p),'format':{'designation':'Exhibit'}},package['profile_id'])
        self.assertEqual(self.s.load(p['id'])['applied_profile'],version)

    def test_corrupted_embedded_version_is_rejected_without_rewriting_project(self):
        package=self.profiles.create('Rules',capture(self.p));version=package['versions'][0]
        args=(self.p['id'],package['profile_id'],version['version_id'],OPTIONS)
        _,preview=self.profiles.preview(*args);p=self.profiles.apply(*args,preview['token'])
        p['applied_profile']['name']='Tampered rules';self.s.save(p)
        file=self.s.folder(p['id'])/'project.json';before=file.read_bytes()
        with self.assertRaisesRegex(ValueError,'сумма'):self.s.load(p['id'])
        self.assertEqual(file.read_bytes(),before)

    def test_explicit_reset_conflict_and_stale_never_partially_apply(self):
        package=self.profiles.create('Reset',capture(self.p));v=package['versions'][0]
        args=(self.p['id'],package['profile_id'],v['version_id'],{**OPTIONS,'reset_overrides':True})
        _,preview=self.profiles.preview(*args)
        self.s.update(self.p,self.did,{'title':'Changed'})
        with self.assertRaisesRegex(ValueError,'изменились'):self.profiles.apply(*args,preview['token'])
        self.assertIsNone(self.s.load(self.p['id']).get('applied_profile'))
        _,preview=self.profiles.preview(*args);p=self.profiles.apply(*args,preview['token'])
        self.assertEqual(effective_format(p,p['documents'][0])[0]['designation'],'Exhibit')

    def test_corruption_unknown_fields_and_identity_conflicts_are_rejected(self):
        package=self.profiles.create('Rules',capture(self.p));broken=deepcopy(package)
        broken['versions'][0]['rules']['format']['size']=99
        with self.assertRaises(ValueError):self.profiles.import_package(json.dumps(broken).encode())
        with self.assertRaises(ValueError):self.profiles.create('Unknown',{**capture(self.p),'secret':'x'})
        bad=capture(self.p);bad['volumes']['reference_pages']={'x':{}}
        with self.assertRaises(ValueError):self.profiles.create('Bad binding',bad)

    def test_bound_profile_survives_backup_without_global_catalog(self):
        from exhibit import backup
        package=self.profiles.create('Portable',capture(self.p));v=package['versions'][0]
        args=(self.p['id'],package['profile_id'],v['version_id'],OPTIONS)
        _,preview=self.profiles.preview(*args);p=self.profiles.apply(*args,preview['token'])
        path=Path(self.temp.name)/'copy.zip';backup.save_archive(self.s,p['id'],path)
        restored=backup.inspect_archive(path)['project']
        self.assertEqual(restored['applied_profile'],v)
        self.assertEqual(restored['documents'][0]['filename'],'Manual.pdf')
        with TemporaryDirectory() as destination:
            target=Store(destination);copied=backup.restore_archive(target,path,copy=True)
            self.assertEqual(copied['applied_profile'],v)
            self.assertFalse((target.root/'.submission-profiles').exists())
