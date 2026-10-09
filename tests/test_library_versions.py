from copy import deepcopy
from hashlib import sha256
from unittest.mock import patch
from exhibit import backup
from exhibit.library import translation_valid
from exhibit.project import digest
from exhibit.samples import make_pdf
from unittest import TestCase
from tests import test_library


class VersionTests(TestCase):
    setUpClass = classmethod(test_library.LibraryTests.setUpClass.__func__)
    setUp = test_library.LibraryTests.setUp
    save = test_library.LibraryTests.save
    assignments = test_library.LibraryTests.assignments
    def imported(self, source, number):
        p=self.store.create('Target '+str(number))
        plan=self.library.import_preview(p['id'],[self.assignments(source,number)])
        self.library.import_apply(plan['token']); p=self.store.load(p['id'])
        self.store.approve(p,p['documents'][0]['id'],'document')
        self.store.export(p)
        return p

    def v2(self, source, **changes):
        new=make_pdf('New translation',[['A new translated paragraph']])
        self.store.upload(self.p,'Translation v2.pdf',new,'translation',self.d['id'])
        self.store.approve(self.p,self.d['id'],'translation')
        self.store.update(self.p,self.d['id'],changes)
        return self.save(material_id=source['material_id'],comment='Перевод исправлен')[1]

    def test_selective_update_and_rollback_preserve_other_project_sources_and_history(self):
        _,v1=self.save(); a=self.imported(v1,3); b=self.imported(v1,12)
        def tree(p): return {n:sha256(f.read_bytes()).hexdigest() for n,f in backup.inventory(self.store.folder(p['id']))}
        old_a=tree(a); old_b=tree(b); v2=self.v2(v1,title='Новое название')
        self.assertEqual(tree(a),old_a);self.assertEqual(tree(b),old_b)
        status=self.library.notifications(b['id'])[0];self.assertTrue(status['newer'])
        diff=self.library.compare(v1['library_id'],v1['material_id'],v1['version_id'],v2['version_id'])
        self.assertTrue(next(x['changed'] for x in diff['changes'] if x['field']=='translation'))
        self.assertFalse(next(x['changed'] for x in diff['changes'] if x['field']=='original'))
        plan=self.library.update_preview(b['id'],b['documents'][0]['id'],v2['version_id'])
        self.assertFalse(plan['conflicts']); self.library.update_apply(plan['token']); b2=self.store.load(b['id'])
        d=b2['documents'][0];self.assertEqual(d['number'],12);self.assertEqual(d['id'],b['documents'][0]['id'])
        self.assertEqual(d['filename'],'Annex 12.pdf');self.assertTrue(translation_valid(d));self.assertIsNone(d['approved'])
        self.assertEqual(tree(a),old_a)
        self.assertTrue(all(tree(b2)[n]==h for n,h in old_b.items() if n!='project.json'))
        self.assertEqual(self.library.update_apply(plan['token'])['id'],b['id'])
        self.store.approve(b2,d['id'],'document');self.store.export(b2)
        rollback=self.library.update_preview(b['id'],d['id'],v1['version_id'])
        self.library.update_apply(rollback['token']);self.assertEqual(self.store.load(b['id'])['documents'][0]['title'],b['documents'][0]['title'])
        self.assertEqual(tree(a),old_a)

    def test_local_conflicts_cancel_stale_plan_and_failed_write_preserve_project(self):
        _,v1=self.save(); b=self.imported(v1,12);v2=self.v2(v1,title='Remote title')
        self.store.update(b,b['documents'][0]['id'],{'title':'Local title'})
        before=(self.store.folder(b['id'])/'project.json').read_bytes()
        plan=self.library.update_preview(b['id'],b['documents'][0]['id'],v2['version_id'])
        self.assertIn('title',plan['conflicts'])
        with self.assertRaises(ValueError):self.library.update_apply(plan['token'])
        plan=self.library.update_preview(b['id'],b['documents'][0]['id'],v2['version_id'],{'title':'local'})
        with self.assertRaises(ValueError):self.library.update_apply(plan['token'],cancel=lambda:True)
        with patch.object(self.store,'save',side_effect=OSError('full disk')):
            with self.assertRaisesRegex(ValueError,'снимок'): self.library.update_apply(plan['token'])
        self.assertEqual((self.store.folder(b['id'])/'project.json').read_bytes(),before)
        snapshots=list((self.store.root/'backups').glob('before-material-update*.zip'))
        self.assertTrue(snapshots);self.assertEqual(digest(backup.inspect_archive(snapshots[0])['project']),digest(self.store.load(b['id'])))
        self.library.update_apply(plan['token']);self.assertEqual(self.store.load(b['id'])['documents'][0]['title'],'Local title')
        fresh=self.library.update_preview(b['id'],b['documents'][0]['id'],v1['version_id'],{'title':'local'})
        b=self.store.load(b['id']);self.store.update(b,b['documents'][0]['id'],{'language':'en'})
        with self.assertRaisesRegex(ValueError,'изменилась'):self.library.update_apply(fresh['token'])

    def test_corrupt_same_identity_and_missing_library_never_update_silently(self):
        _,v1=self.save();b=self.imported(v1,12)
        path=self.library.folder(v1['library_id'])/'versions'/(v1['version_id']+'.json')
        import json
        v=json.loads(path.read_text('utf-8'));v['metadata']['title']='Forged';path.write_text(json.dumps(v),'utf-8')
        status=self.library.notifications(b['id'])[0];self.assertFalse(status['available'])
        with self.assertRaises(ValueError):self.library.update_preview(b['id'],b['documents'][0]['id'],v1['version_id'])
        self.library.root.rename(self.store.root/'unavailable');self.assertFalse(self.library.notifications(b['id'])[0]['available'])
        self.assertTrue(self.store.export(b))

    def test_new_original_does_not_confirm_retained_old_translation(self):
        _,v1=self.save();b=self.imported(v1,12)
        self.store.upload(self.p,'Changed.pdf',make_pdf('Changed',[['Changed original']]),'original',self.d['id'])
        _,v2=self.save(material_id=v1['material_id'],original_only=True)
        plan=self.library.update_preview(b['id'],b['documents'][0]['id'],v2['version_id'],{'translation':'local','translation_selection':'local'})
        self.assertTrue(plan['translation_review']);self.assertFalse(plan['document']['translation_confirmed'])

    def test_removed_part_and_invalid_retained_page_range_are_explicit(self):
        _,v1=self.save();b=self.imported(v1,12)
        self.store.upload(self.p,'Longer.pdf',make_pdf('Longer',[['One'],['Two'],['Three']]),'original',self.d['id'])
        _,v2=self.save(material_id=v1['material_id'],original_only=True)
        diff=self.library.compare(v1['library_id'],v1['material_id'],v1['version_id'],v2['version_id'])
        removed=next(r for r in diff['changes'] if r['field']=='translation');self.assertTrue(removed['changed']);self.assertIsNone(removed['after'])
        self.library.update_apply(self.library.update_preview(b['id'],b['documents'][0]['id'],v2['version_id'])['token'])
        b=self.store.load(b['id']);self.store.update(b,b['documents'][0]['id'],{'selection':[{'page':3}]})
        plan=self.library.update_preview(b['id'],b['documents'][0]['id'],v1['version_id'],{'selection':'local'})
        self.assertTrue(plan['errors'])
        with self.assertRaises(ValueError):self.library.update_apply(plan['token'])
        self.assertEqual(self.store.load(b['id'])['documents'][0]['selection'],[{'page':3}])
        v=self.library.version(v1['library_id'],v1['material_id'],v2['version_id'])
        (self.library.folder(v1['library_id'])/'blobs'/v['parts']['original']['blob']).write_bytes(b'bad')
        with self.assertRaises(ValueError):self.library.compare(v1['library_id'],v1['material_id'],v1['version_id'],v2['version_id'])


