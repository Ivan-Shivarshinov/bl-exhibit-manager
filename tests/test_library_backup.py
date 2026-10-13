from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED
import json
import stat

from tests import test_library
from exhibit import backup,library_backup
from exhibit.library import Library
from exhibit.project import Store,digest


class ArchiveTests(TestCase):
    setUpClass=classmethod(test_library.LibraryTests.setUpClass.__func__)
    setUp=test_library.LibraryTests.setUp
    save=test_library.LibraryTests.save
    assignments=test_library.LibraryTests.assignments

    def archive(self):
        _,self.v1=self.save()
        _,self.v2=self.save(material_id=self.v1['material_id'],allow_duplicate=True,comment='Иные реквизиты',metadata={'category':'Evidence'})
        result=self.library.archive_save(self.lib['id']);self.path=self.library.pending(result['token'])/'library.zip'
        return result

    def test_all_versions_identity_copy_conflict_and_independent_project(self):
        result=self.archive()
        with patch.object(self.library,'load',wraps=self.library.load) as loads:
            library_backup.save(self.library,self.lib['id'],self.store.root/'snapshot.zip')
            self.assertEqual(loads.call_count,1)
        checked=library_backup.inspect(self.path)
        self.assertEqual(checked['summary']['versions'],2)
        self.assertEqual(checked['library']['operations'],{})
        with TemporaryDirectory() as tmp:
            target=Library(Store(tmp));restored=library_backup.restore(target,self.path)
            self.assertEqual(restored['id'],self.lib['id'])
            self.assertEqual(digest(target.version(**{'lid':self.v1['library_id'],'mid':self.v1['material_id'],'vid':self.v1['version_id']})),digest(self.library.version(self.lib['id'],self.v1['material_id'],self.v1['version_id'])))
            with self.assertRaises(ValueError):library_backup.restore(target,self.path)
            copied=library_backup.restore(target,self.path,copy=True)
            self.assertNotEqual(copied['id'],self.lib['id'])
            card=next(iter(target.load(copied['id'])['materials'].values()))
            self.assertNotEqual(card['id'],self.v1['material_id'])
            self.assertEqual(target.version(copied['id'],card['id'])['parent'],card['versions'][0])
        p=self.store.create('Portable');plan=self.library.import_preview(p['id'],[self.assignments(self.v1)])
        self.library.import_apply(plan['token']);p=self.store.load(p['id']);self.store.approve(p,p['documents'][0]['id'],'document');self.store.export(p)
        pzip=self.store.root/'project.zip';backup.save_archive(self.store,p['id'],pzip)
        with TemporaryDirectory() as tmp:
            store=Store(tmp); restored=backup.restore_archive(store,pzip)
            self.assertTrue(store.export(restored));lib=Library(store)
            self.assertFalse(lib.notifications(restored['id'])[0]['available'])
            library_backup.restore(lib,self.path);self.assertTrue(lib.notifications(restored['id'])[0]['available'])

    def test_cancel_and_disk_failure_never_publish_restore_or_archive(self):
        self.archive()
        with TemporaryDirectory() as tmp:
            target=Library(Store(tmp))
            with self.assertRaises(ValueError):library_backup.restore(target,self.path,cancel=lambda:True)
            self.assertEqual(target.list(),[])
            with patch('exhibit.library_backup.os.replace',side_effect=OSError('disk full')):
                with self.assertRaises(ValueError):library_backup.restore(target,self.path)
            self.assertEqual(target.list(),[])
        broken=self.store.root/'cancelled.zip'
        with self.assertRaises(ValueError):library_backup.save(self.library,self.lib['id'],broken,cancel=lambda:True)
        self.assertFalse(broken.exists())
        previous=self.path.read_bytes()
        with self.assertRaises(ValueError):library_backup.save(self.library,self.lib['id'],self.path)
        self.assertEqual(self.path.read_bytes(),previous)

    def test_tampered_traversal_symlink_duplicate_future_missing_and_wrong_type_rejected(self):
        self.archive()
        with ZipFile(self.path) as z: contents={i.filename:z.read(i) for i in z.infolist()}
        variations=[]
        variations.append({**contents,'../secret.txt':b'no'})
        manifest=json.loads(contents['backup.json']);manifest['format']=999
        variations.append({**contents,'backup.json':json.dumps(manifest).encode()})
        variations.append({**contents,'library.json':b'{}'})
        variations.append({k:v for k,v in contents.items() if not k.startswith('blobs/')})
        variations.append({**contents,'backup.json':json.dumps({'application':'exhibit-manager-project','format':1}).encode()})
        for n,c in enumerate(variations):
            path=self.store.root/f'invalid-{n}.zip'
            with ZipFile(path,'w') as z:
                for k,v in c.items():z.writestr(k,v)
            with self.subTest(n=n),self.assertRaises(ValueError):library_backup.inspect(path)
        for kind in ('symlink','duplicate'):
            path=self.store.root/(kind+'.zip')
            with ZipFile(path,'w') as z:
                for k,v in contents.items():z.writestr(k,v)
                if kind=='duplicate':z.writestr('library.json',contents['library.json'])
                else:
                    info=ZipInfo('blobs/'+('a'*64)+'.pdf');info.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(info,b'/private')
            with self.assertRaises(ValueError):library_backup.inspect(path)

    def test_valid_checksums_do_not_allow_forged_pdf_metadata_and_limits(self):
        self.archive()
        with ZipFile(self.path) as z: contents={i.filename:z.read(i) for i in z.infolist()}
        name='versions/'+self.v1['version_id']+'.json'
        version=json.loads(contents[name]);version['parts']['original']['sizes'][0][0]+=10
        contents[name]=json.dumps(version).encode()
        manifest=json.loads(contents['backup.json'])
        from hashlib import sha256
        manifest['files'][name]={'sha256':sha256(contents[name]).hexdigest(),'size':len(contents[name])}
        contents['backup.json']=json.dumps(manifest).encode()
        forged=self.store.root/'forged.zip'
        with ZipFile(forged,'w') as z:
            for k,v in contents.items():z.writestr(k,v)
        with self.assertRaisesRegex(ValueError,'страницах'):library_backup.inspect(forged)
        for limit in ('MAX_BYTES','MAX_FILES','MAX_JSON','MAX_INPUT'):
            with patch('exhibit.library_backup.'+limit,1),self.subTest(limit=limit),self.assertRaises(ValueError):library_backup.inspect(self.path)
        # Deterministic copy retries compare one manifest at a time and retain IDs.
        with TemporaryDirectory() as tmp:
            target=Library(Store(tmp));first=library_backup.restore(target,self.path,copy=True,identity_seed='retry')
            self.assertEqual(first,library_backup.restore(target,self.path,copy=True,identity_seed='retry'))
