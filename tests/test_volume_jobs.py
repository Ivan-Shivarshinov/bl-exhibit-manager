from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic,sleep
from unittest import TestCase
from unittest.mock import patch
from threading import Event
from uuid import uuid4
from zipfile import ZipFile

from exhibit.project import Store
from exhibit import volume_settings,history
from exhibit.volume_jobs import VolumeJobs
from tests.test_volumes import sample


class VolumeService(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.p=self.store.create('Synthetic volumes')
        for n in range(2):self.store.upload(self.p,f'Annex {n+1}.pdf',sample(str(n)),mode='passthrough')
        self.p=volume_settings.save(self.store,self.p,{**deepcopy(volume_settings.DEFAULT),'enabled':True})
        self.jobs=VolumeJobs(self.store);self.store.volume_jobs=self.jobs;self.addCleanup(self.jobs.close)

    def wait(self,token):
        until=monotonic()+15
        while monotonic()<until:
            state=self.jobs.status(token)
            if state['state'] not in ('queued','running'):return state
            sleep(.02)
        self.fail('Volume operation timed out')

    def test_preview_export_archive_sources_history_and_disabled_path(self):
        before=[self.store.source(self.p,d['original']) for d in self.p['documents']]
        plan=self.jobs.preview(self.p['id'])
        data=self.store.export(self.p,plan['preview_token'])
        with ZipFile(BytesIO(data)) as z:
            self.assertIn('Submission/Volumes/Volume 01.pdf',z.namelist())
            self.assertIn('Submission/Volumes/Volume map.json',z.namelist())
            for d,b in zip(self.p['documents'],before):self.assertEqual(z.read('Submission/'+d['filename']),b)
        self.assertEqual(len(history.list_exports(self.store,self.p)),1)
        snapshot=history.list_exports(self.store,self.p)[0]['manifest']
        self.assertEqual(len(snapshot['volume_files']),1)
        self.assertEqual(snapshot['volume_files'][0]['sha256'],plan['volumes'][0]['sha256'])
        self.assertTrue(snapshot['volume_map_sha256'])
        normal=self.store.export(self.p,include_volumes=False)
        with ZipFile(BytesIO(normal)) as z:self.assertFalse(any('Volumes/' in n for n in z.namelist()))
        from exhibit import backup
        path=Path(self.temp.name)/'copy.zip';backup.save_archive(self.store,self.p['id'],path)
        checked=backup.inspect_archive(path)
        self.assertEqual(checked['project']['volume_settings'],self.p['volume_settings'])
        self.assertEqual(len(history.list_exports(self.store,self.p)),2)
        self.assertIsNone(history.list_exports(self.store,self.p)[0]['manifest']['volume_settings'])

    def test_stale_preview_and_corrupt_prepared_result_never_publish(self):
        plan=self.jobs.preview(self.p['id']);self.p['name']='Changed';self.store.save(self.p)
        with self.assertRaisesRegex(ValueError,'изменилась'):self.jobs.export_bytes(self.p['id'],plan['preview_token'])
        self.assertFalse(history.list_exports(self.store,self.p))
        plan=self.jobs.preview(self.p['id'])
        root=Path(self.jobs.plans[self.p['id']]['temporary'].name)
        (root/'Volume 01.pdf').write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError,'изменён'):self.jobs.export_bytes(self.p['id'],plan['preview_token'])
        self.assertFalse(history.list_exports(self.store,self.p))

    def test_background_jobs_download_and_cleanup(self):
        job=self.jobs.start(self.p['id'],'preview')['id'];state=self.wait(job)
        self.assertEqual(state['state'],'complete',state)
        export=self.jobs.start(self.p['id'],'export',state['result']['preview_token'])['id'];state=self.wait(export)
        self.assertEqual(state['state'],'complete',state)
        path=self.jobs.download(export);self.assertTrue(path.is_file())
        self.jobs.release(export);self.assertFalse(path.exists())
        self.assertEqual(len(history.list_exports(self.store,self.p)),1)

    def test_frozen_acceptance_contract_against_api(self):
        from exhibit import app
        from tests.api_client import TestClient
        from scripts.volume_checks import verify
        with patch.object(app,'store',self.store):
            client=TestClient(app.app,base_url='http://localhost')
            def request(path,body=None,raw=None,method=None):
                response=client.request(method or ('POST' if body is not None or raw is not None else 'GET'),path,
                    json=body,content=raw,headers={'X-Exhibit-Local':'1'})
                self.assertEqual(response.status_code,200,response.text[:500])
                return response.json() if 'json' in response.headers.get('content-type','') else response.content
            verify(request,Path(self.temp.name),Path(self.temp.name)/'exchange')

    def test_api_settings_job_preview_image_export_and_stale(self):
        from exhibit import app
        from tests.api_client import TestClient
        with patch.object(app,'store',self.store):
            client=TestClient(app.app,base_url='http://localhost');h={'X-Exhibit-Local':'1'};url='/api/projects/'+self.p['id']
            res=client.post(url+'/volumes/preview',json={},headers=h);self.assertEqual(res.status_code,200,res.text)
            state=self.wait(res.json()['id']);self.assertEqual(state['state'],'complete',state)
            plan=state['result']
            image=client.get(url+f"/volumes/{plan['preview_token']}/0/image?page=1")
            self.assertEqual(image.status_code,200,image.text[:100] if image.status_code!=200 else '')
            self.assertEqual(image.content[:8],b'\x89PNG\r\n\x1a\n')
            data=client.post(url+'/export',json={'preview_token':plan['preview_token']},headers=h)
            self.assertEqual(data.status_code,200,data.text[:100] if data.status_code!=200 else '')
            bad=client.post(url+'/volumes/settings',json={**self.p['volume_settings'],'start':False},headers=h)
            self.assertEqual(bad.status_code,400)
            self.p['name']='Changed from another window';self.store.save(self.p)
            self.assertEqual(client.get(url+f"/volumes/{plan['preview_token']}/0/pdf").status_code,400)

    def test_cancel_during_preparation_removes_owned_temporary_and_preserves_history(self):
        entered=Event();release=Event();real=self.store.prepare
        def pause(*args):entered.set();release.wait(5);return real(*args)
        before=(self.store.folder(self.p['id'])/'project.json').read_bytes()
        with patch.object(self.store,'prepare',side_effect=pause):
            token=self.jobs.start(self.p['id'],'preview')['id'];self.assertTrue(entered.wait(3))
            self.jobs.cancel(token);release.set();state=self.wait(token)
        self.assertEqual(state['state'],'cancelled',state)
        self.assertEqual((self.store.folder(self.p['id'])/'project.json').read_bytes(),before)
        self.assertFalse(history.list_exports(self.store,self.p));self.assertFalse(list(self.jobs.work.iterdir()))

    def test_disk_failure_does_not_publish_or_leave_busy(self):
        plan=self.jobs.preview(self.p['id'])
        with patch('exhibit.history.save_export',side_effect=OSError('full')):
            token=self.jobs.start(self.p['id'],'export',plan['preview_token'])['id'];state=self.wait(token)
        self.assertEqual(state['state'],'error',state);self.assertFalse(self.jobs.busy)
        self.assertFalse(history.list_exports(self.store,self.p))

    def test_restart_marks_interrupted_and_cleans_only_owned_dead_process_work(self):
        from exhibit.library import atomic_json
        self.jobs.close();token=uuid4().hex
        atomic_json(self.jobs.states/(token+'.json'),{'id':token,'project_id':self.p['id'],'state':'running'})
        atomic_json(self.jobs.states/'owner',{'pid':999999999})
        abandoned=self.jobs.work/'snapshot-abandoned';abandoned.mkdir();(abandoned/'partial.pdf').write_bytes(b'partial')
        retained=self.jobs.work/'unrecognized';retained.mkdir();(retained/'keep.txt').write_text('keep')
        service=VolumeJobs(self.store);self.addCleanup(service.close)
        self.assertEqual(service.status(token)['state'],'interrupted')
        self.assertFalse(abandoned.exists());self.assertTrue((retained/'keep.txt').is_file())

    def test_two_citations_same_material_go_to_different_exact_pages_across_volumes(self):
        from pypdf import PdfReader,PdfWriter
        from exhibit import volume_pages
        from exhibit.project import revision
        from tests.test_feedback import citation_docx
        did=self.p['documents'][0]['id']
        self.store.upload(self.p,'Replacement.pdf',sample('TWO PAGES',2),did=did)
        self.store.update(self.p,did,{'designation':'Annex','number':1,'title':'Same source'})
        self.store.update(self.p,self.p['documents'][1]['id'],{'designation':'Annex','number':2})
        self.store.upload(self.p,'Main.docx',citation_docx(['Annex 1, Same source, p. 1.','Annex 1, Same source, p. 2.']),'main')
        self.store.scan(self.p);self.store.confirm_links(self.p)
        refs=self.p['references'];self.assertEqual(len(refs),2)
        config={**self.p['volume_settings'],'include_main':True,'max_pages':4,
                'reference_pages':{r['key']:{'document_id':did,'revision':revision(self.p,self.p['documents'][0]),'page':n+1} for n,r in enumerate(refs)}}
        self.p=volume_settings.save(self.store,self.p,config)
        def converter(data,paths):
            self.assertEqual(len(paths),2);self.assertEqual(len(set(paths)),2)
            writer=PdfWriter(clone_from=BytesIO(sample('MAIN DOCUMENT')))
            for n,path in enumerate(paths):
                annotation=volume_pages.link((20,400-n*30,180,420-n*30),'')
                annotation[__import__('pypdf').generic.NameObject('/A')]=volume_pages.file_action(path,0);writer.add_annotation(0,annotation)
            output=BytesIO();writer.write(output);return output.getvalue()
        with patch('exhibit.main_pdf.convert_docx',side_effect=converter):plan=self.jobs.preview(self.p['id'])
        self.assertFalse(plan['issues'],plan)
        package=self.jobs.export_bytes(self.p['id'],plan['preview_token'])
        with ZipFile(BytesIO(package)) as archive:
            main=PdfReader(BytesIO(archive.read('Submission/Volumes/Volume 01.pdf')))
            targets=[a.get_object()['/A'] for a in main.pages[2]['/Annots'] if a.get_object().get('/BLVolumeLink') and a.get_object().get('/A',{}).get('/S')=='/GoToR']
            self.assertEqual([int(a['/D'][0]) for a in targets],[2,3])
            self.assertTrue(all(a['/F']['/UF']=='Volume 02.pdf' for a in targets))
            normal=PdfReader(BytesIO(archive.read('Submission/Main document.pdf')))
            normal_targets=[a.get_object()['/A'] for a in normal.pages[0]['/Annots'] if a.get_object().get('/A',{}).get('/S')=='/GoToR']
            self.assertEqual([int(a['/D'][0]) for a in normal_targets],[0,1])
            self.assertTrue(all(a['/F']['/UF']=='Annex 1.pdf' for a in normal_targets))
        self.store.update(self.p,did,{'title':'Changed source revision'})
        with patch('exhibit.main_pdf.convert_docx',side_effect=converter):changed=self.jobs.preview(self.p['id'])
        self.assertTrue(any('Цель ссылки устарела' in v for v in changed['issues']))
        with self.assertRaisesRegex(ValueError,'устарела'):self.jobs.export_bytes(self.p['id'],changed['preview_token'])
