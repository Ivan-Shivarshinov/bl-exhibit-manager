from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from fastapi.testclient import TestClient
from exhibit.project import Store
from exhibit import index_settings
from exhibit.samples import make_pdf


class IndexAPI(TestCase):
    def setUp(self):
        from exhibit import app
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=Store(self.temp.name);self.p=self.s.create('API example')
        self.s.upload(self.p,'File.pdf',make_pdf('Example',[['Example']]),mode='passthrough')
        self.patch=patch.object(app,'store',self.s);self.patch.start();self.addCleanup(self.patch.stop)
        self.addCleanup(lambda:getattr(self.s,'index_plans',None) and self.s.index_plans.close())
        self.client=TestClient(app.app,base_url='http://localhost');self.h={'X-Exhibit-Local':'1'}
        self.path='/api/projects/'+self.p['id']

    def test_settings_preview_export_stale_and_validation(self):
        before=(self.s.folder(self.p['id'])/'project.json').read_bytes()
        self.assertFalse(self.client.get(self.path+'/submission-index').json()['enabled'])
        self.assertEqual(before,(self.s.folder(self.p['id'])/'project.json').read_bytes())
        self.assertEqual(self.client.patch(self.path+'/submission-index',headers=self.h,json={'enabled':True}).status_code,400)
        config={**deepcopy(index_settings.DEFAULT),'enabled':True}
        response=self.client.patch(self.path+'/submission-index',headers=self.h,json=config)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['schema'],3)
        self.assertEqual(self.client.post(self.path+'/export',headers=self.h,json={}).status_code,400)
        preview=self.client.post(self.path+'/submission-index/preview',headers=self.h,json={})
        self.assertEqual(preview.status_code,200,preview.text)
        token=preview.json()['preview_token']
        self.assertEqual(self.client.post(self.path+'/export',headers=self.h,json={'preview_token':token}).status_code,200)
        p=self.s.load(self.p['id']);p['documents'][0]['title']='Edited';self.s.save(p)
        self.assertEqual(self.client.post(self.path+'/export',headers=self.h,json={'preview_token':token}).status_code,400)
        self.assertEqual(self.client.get('/api/projects/not-an-id/submission-index').status_code,400)
        self.assertEqual(self.client.post('/api/projects/'+'f'*32+'/submission-index/preview',headers=self.h,json={}).status_code,400)

    def test_disabled_legacy_export_requires_no_index_token(self):
        response=self.client.post(self.path+'/export',headers=self.h,json={})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.s.load(self.p['id'])['schema'],1)
