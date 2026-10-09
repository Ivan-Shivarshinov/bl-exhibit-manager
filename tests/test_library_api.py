from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import time
from fastapi.testclient import TestClient

from exhibit.project import Store
from exhibit.samples import make_pdf
from exhibit.library import Library
from exhibit.material_jobs import Jobs


class LibraryAPI(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name)
        import exhibit.app as module
        self.module=module;self.patched=patch.object(module,'store',self.store);self.patched.start();self.addCleanup(self.patched.stop)
        self.client=TestClient(module.app,base_url="http://127.0.0.1");self.headers={'X-Exhibit-Local':'1'}

    def post(self,path,body):
        response=self.client.post('/api'+path,json=body,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text);return response.json()

    def operation(self,action,arguments):
        token=self.post('/material-operations',{'action':action,'arguments':arguments})['id']
        for _ in range(250):
            state=self.client.get('/api/material-operations/'+token).json()
            if state['state']=='complete': return state['result']
            if state['state']=='error': self.fail(state['message'])
            time.sleep(.02)
        self.fail('Operation did not complete')

    def test_end_to_end_jobs_catalog_preview_import_and_export_without_panel(self):
        lib=self.post('/libraries',{'name':'Case'})
        p=self.post('/projects',{'name':'Source'});pid=p['id']
        response=self.client.post(f'/api/projects/{pid}/upload?name=Finished.pdf&mode=passthrough',content=make_pdf('PDF',[['Example']]),headers=self.headers)
        self.assertEqual(response.status_code,200,response.text);p=response.json()
        plan=self.operation('save_preview',{'lid':lib['id'],'pid':pid,'rows':[{'document_id':p['documents'][0]['id']}]})
        source=self.operation('publish',{'token':plan['token']})['materials'][0]
        rows=self.client.get(f"/api/libraries/{lib['id']}/materials?q=Finished").json();self.assertEqual(rows['total'],1)
        prefix=f"/api/libraries/{lib['id']}/materials/{source['material_id']}/versions/{source['version_id']}"
        version=self.client.get(prefix).json();self.assertEqual(set(version['parts']),{'ready'})
        self.assertTrue(self.client.get(prefix+'/preview?role=ready&page=1').content.startswith(b'\x89PNG'))
        target=self.post('/projects',{'name':'Target'})
        plan=self.operation('import_preview',{'pid':target['id'],'rows':[{**source,'mode':'passthrough','number':12,'designation':'Annex','filename':'Annex 12.pdf'}]})
        self.assertFalse(plan['errors']);result=self.operation('import_apply',{'token':plan['token']});self.assertEqual(len(result['documents']),1)
        # Catalog read is independent of Word installation and translator accounts.
        self.assertEqual(self.client.get('/api/word/status').json()['configured'],False)

    def test_jobs_cancel_and_restart_never_claim_success(self):
        library=Library(self.store);jobs=Jobs(library)
        started=jobs.start('import_preview',{'pid':'0'*32,'rows':[]})
        while started['id'] in jobs.active: time.sleep(.01)
        self.assertEqual(jobs.status(started['id'])['state'],'error')
        from exhibit.library import atomic_json
        token='1'*32;atomic_json(jobs.root/(token+'.json'),{'id':token,'state':'running'})
        reopened=Jobs(library);self.assertEqual(reopened.status(token)['state'],'interrupted')
