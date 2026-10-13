from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zipfile import ZipFile

from exhibit.library import Library, atomic_json
from exhibit.project import Store
from tests.pdf_forms import form_pdf


class ReadyLibraryForms(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=Store(self.temp.name);self.l=Library(self.s);self.lib=self.l.create('Synthetic forms')

    def test_ready_role_round_trip_without_reformat_or_source_changes(self):
        for kind in ('form','widget','unsigned','signed'):
            data=form_pdf(kind);source=self.s.create(kind)
            self.s.upload(source,'Annex 65.pdf',data,mode='passthrough')
            before=deepcopy(source)
            plan=self.l.save_preview(self.lib['id'],source['id'],[{'document_id':source['documents'][0]['id']}])
            self.assertFalse(plan['errors']);ref=self.l.publish(plan['token'])['materials'][0]
            self.assertEqual(before,self.s.load(source['id']))
            self.assertEqual(set(self.l.version(ref['library_id'],ref['material_id'],verify=True)['parts']),{'ready'})
            target=self.s.create('Independent ready copy')
            row={**ref,'mode':'passthrough','number':12,'designation':'Annex','folder':'Evidence','filename':'Annex 12.pdf'}
            preview=self.l.import_preview(target['id'],[row]);self.assertFalse(preview['errors'])
            self.l.import_apply(preview['token']);target=self.s.load(target['id']);d=target['documents'][0]
            with ZipFile(BytesIO(self.s.export(target))) as z:self.assertEqual(z.read('Submission/Evidence/Annex 12.pdf'),data)
            with self.assertRaisesRegex(ValueError,'Добавить готовые PDF'):self.s.update(target,d['id'],{'mode':'prepare'})
            self.assertTrue(self.l.import_preview(self.s.create('Blocked')['id'],[{**row,'mode':'prepare'}])['errors'])
            saved=self.l.archive_save(self.lib['id']);raw=(self.l.pending(saved['token'])/'library.zip').read_bytes()
            folder=self.l.pending();(folder/'library.zip').write_bytes(raw)
            preview=self.l.archive_preview(folder.name);copy=self.l.archive_restore(preview['token'],copy=True)
            restored=self.l.load(copy['id'])
            self.assertEqual(len(restored['materials']),len(self.l.load(self.lib['id'])['materials']))
            for mid in restored['materials']:
                v=self.l.version(restored['id'],mid,verify=True)
                self.assertEqual(set(v['parts']),{'ready'})
            self.assertEqual(before,self.s.load(source['id']))

    def test_role_is_checked_after_role_free_receipt(self):
        data=form_pdf('widget');folder=self.l.pending()
        part=self.l.stage_part(folder,'Form.pdf',data,allow_forms=True)
        atomic_json(folder/'part.json',part)
        original=self.l.upload_preview(self.lib['id'],[{'token':folder.name,'role':'original','metadata':{'title':'Form'}}])
        self.assertTrue(original['errors']);self.assertIn('Добавить готовые PDF',original['errors'][0]['message'])
        ready=self.l.upload_preview(self.lib['id'],[{'token':folder.name,'role':'ready','metadata':{'title':'Form'}}])
        self.assertFalse(ready['errors']);self.l.publish(ready['token'])

    def test_shared_blob_does_not_inherit_ready_permission_for_original(self):
        from exhibit.library_backup import summary
        data=form_pdf('widget');source=self.s.create('Ready source')
        self.s.upload(source,'Ready.pdf',data,mode='passthrough')
        plan=self.l.save_preview(self.lib['id'],source['id'],[{'document_id':source['documents'][0]['id']}])
        ref=self.l.publish(plan['token'])['materials'][0]
        v=self.l.version(ref['library_id'],ref['material_id'])
        v['parts']['original']=deepcopy(v['parts']['ready'])
        v['recipe']['selection']=[{'page':1},{'page':2}]
        atomic_json(self.l.folder(self.lib['id'])/'versions'/(v['id']+'.json'),v)
        lib=self.l.load(self.lib['id']);lib['materials'][ref['material_id']]['latest']=summary(v)
        atomic_json(self.l.folder(self.lib['id'])/'library.json',lib)
        with self.assertRaisesRegex(ValueError,'Добавить готовые PDF'):self.l.archive_save(self.lib['id'])
