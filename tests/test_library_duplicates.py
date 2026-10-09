from copy import deepcopy
from unittest import TestCase
from exhibit.samples import make_pdf
from exhibit.library import atomic_json
from tests import test_library_bulk


class HistoricalDuplicates(TestCase):
    setUpClass=classmethod(test_library_bulk.BulkTests.setUpClass.__func__)
    setUp=test_library_bulk.BulkTests.setUp
    save=test_library_bulk.BulkTests.save
    uploaded=test_library_bulk.BulkTests.uploaded

    def history(self):
        _,v1=self.save()
        self.store.upload(self.p,'Changed translation.pdf',make_pdf('New',[['Changed translation']]),'translation',self.d['id'])
        self.store.approve(self.p,self.d['id'],'translation')
        _,v2=self.save(material_id=v1['material_id'])
        return v1,v2

    def test_upload_and_save_match_previous_version_and_show_specific_identity(self):
        v1,v2=self.history()
        self.store.upload(self.p,'Translation restored.pdf',self.translation,'translation',self.d['id'])
        self.store.approve(self.p,self.d['id'],'translation')
        saved=self.library.save_preview(self.lib['id'],self.p['id'],[{'document_id':self.d['id']}])
        original=self.uploaded('Renamed original.pdf',self.original)
        translation=self.uploaded('Renamed translation.pdf',self.translation,'translation',pair=original['token'],confirmed=True)
        uploaded=self.library.upload_preview(self.lib['id'],[original,translation])
        for plan in (saved,uploaded):
            row=plan['rows'][0];match=plan['matches'][row['duplicates'][0]]
            self.assertEqual(match['material_id'],v1['material_id']);self.assertEqual(match['version_id'],v1['version_id'])
            self.assertEqual(match['version_number'],1);self.assertEqual(match['title'],self.d['title'])
            self.assertEqual(plan['matches'][row['related'][0]]['version_id'],v2['version_id'])
            self.assertEqual(plan['matches'][row['related'][0]]['kind'],'original')
            with self.assertRaisesRegex(ValueError,'явное решение'):self.library.publish(plan['token'])
        # Hidden and off-page cards remain searchable for duplicates.
        self.library.hide(self.lib['id'],v1['material_id'],True,self.library.load(self.lib['id'])['revision'])
        again=self.library.upload_preview(self.lib['id'],[original,translation]);self.assertTrue(again['matches'][again['rows'][0]['duplicates'][0]]['hidden'])
        self.assertEqual(self.library.catalog(self.lib['id'])['total'],0)

    def test_reuse_historical_version_new_version_and_separate_are_explicit_and_idempotent(self):
        v1,v2=self.history();old=self.library.version(self.lib['id'],v1['material_id'],v1['version_id'])
        original=self.uploaded('Original again.pdf',self.original)
        translation=self.uploaded('Translation again.pdf',self.translation,'translation',pair=original['token'],confirmed=True)
        for action in ('reuse','version','separate'):
            plan=self.library.upload_preview(self.lib['id'],[original,translation]);vid=plan['rows'][0]['version']['id']
            decision={vid:{'action':action,**({'version_id':v1['version_id']} if action!='separate' else {})}}
            before=deepcopy(self.library.load(self.lib['id']))
            with self.assertRaisesRegex(ValueError,'отмен'): self.library.publish(plan['token'],cancel=lambda:True,decisions=decision)
            self.assertEqual(self.library.load(self.lib['id']),before)
            result=self.library.publish(plan['token'],decisions=decision)
            self.assertEqual(self.library.publish(plan['token'],decisions=decision),result)
            ref=result['materials'][0]
            if action=='reuse':
                self.assertEqual(ref['version_id'],v1['version_id']);self.assertEqual(self.library.load(self.lib['id'])['materials'],before['materials'])
            elif action=='version':
                self.assertEqual(ref['material_id'],v1['material_id'])
                self.assertEqual(self.library.version(self.lib['id'],ref['material_id'],ref['version_id'])['parent'],v2['version_id'])
            else:self.assertNotEqual(ref['material_id'],v1['material_id'])
            self.assertEqual(self.library.version(self.lib['id'],v1['material_id'],v1['version_id']),old)

    def test_batch_matches_reuse_new_version_and_bad_later_decision_are_atomic(self):
        rows=[self.uploaded(name,self.original) for name in ('First.pdf','Again.pdf','Third.pdf')]
        plan=self.library.upload_preview(self.lib['id'],rows);a,b,c=plan['rows']
        self.assertTrue(plan['matches'][b['duplicates'][0]]['batch']);self.assertEqual(plan['matches'][b['duplicates'][0]]['title'],'First')
        decisions={b['version']['id']:{'action':'reuse','version_id':a['version']['id']},c['version']['id']:{'action':'version','version_id':b['version']['id']}}
        bad={**decisions,c['version']['id']:{'action':'reuse','version_id':'f'*32}}
        before=self.library.load(self.lib['id'])
        with self.assertRaises(ValueError):self.library.publish(plan['token'],decisions=bad)
        self.assertEqual(self.library.load(self.lib['id']),before)
        self.assertTrue(self.library.planned_page(plan['token'],a['version']['id']).startswith(b'\x89PNG'))
        result=self.library.publish(plan['token'],decisions=decisions)['materials']
        self.assertEqual(result[0]['material_id'],result[1]['material_id']);self.assertEqual(result[0]['version_id'],result[1]['version_id'])
        self.assertEqual(result[2]['material_id'],result[0]['material_id'])
        self.assertEqual(self.library.catalog(self.lib['id'])['total'],1)
        self.assertEqual(self.library.catalog(self.lib['id'])['items'][0]['version_count'],2)

    def test_original_only_match_cannot_be_reused_as_exact_and_stale_decisions_fail(self):
        v1,_=self.history();row=self.uploaded('Only original.pdf',self.original)
        plan=self.library.upload_preview(self.lib['id'],[row]);r=plan['rows'][0]
        self.assertFalse(r['duplicates']);self.assertTrue(r['related'])
        with self.assertRaisesRegex(ValueError,'явное решение'):self.library.publish(plan['token'])
        with self.assertRaisesRegex(ValueError,'полным совпадением'):
            self.library.publish(plan['token'],decisions={r['version']['id']:{'action':'reuse','version_id':v1['version_id']}})
        self.library.publish(self.library.upload_preview(self.lib['id'],[self.uploaded('Distinct.pdf',self.translation)])['token'])
        with self.assertRaisesRegex(ValueError,'изменилась'):self.library.publish(plan['token'])
