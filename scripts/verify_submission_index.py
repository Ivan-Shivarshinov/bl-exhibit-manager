"""Synthetic HTTP/browser flow; never opens a user's project or Office."""
import argparse,json,os,socket,subprocess,sys,time
import tracemalloc
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from io import BytesIO
from zipfile import ZipFile
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from exhibit.samples import make_pdf
from exhibit.index_settings import DEFAULT
from exhibit import index_documents
from exhibit.project import Store
from exhibit import index_settings, submission_index
from exhibit.local_access import owner_headers
from exhibit.launch import browser_entry


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='bl-index-qa-') as data:
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        env={**os.environ,'EXHIBIT_DATA_DIR':data}
        with (out/'server.log').open('w',encoding='utf-8') as log:
            proc=subprocess.Popen([sys.executable,'-m','uvicorn','exhibit.app:app','--host','127.0.0.1','--port',str(port),'--no-proxy-headers','--no-access-log'],cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:
                base=f'http://127.0.0.1:{port}';client=httpx.Client(base_url=base,headers={'X-Exhibit-Local':'1'},timeout=120)
                for _ in range(200):
                    try:
                        if client.get('/api/health').is_success:break
                    except httpx.ConnectError:pass
                    time.sleep(.05)
                else:raise RuntimeError('Synthetic server did not start')
                client.headers.update(owner_headers(base,Path(data)))
                def post(route,body):
                    r=client.post('/api'+route,json=body);r.raise_for_status();return r.json()
                p=post('/projects',{'name':'Synthetic index QA'});pid=p['id']
                for n in range(1,101):
                    r=client.post(f'/api/projects/{pid}/upload',params={'name':f'Annex {n}.pdf','mode':'passthrough'},content=make_pdf('Example',[[f'Synthetic item {n}']]))
                    r.raise_for_status()
                    doc=r.json()['documents'][-1]
                    r=client.post(f'/api/projects/{pid}/documents/{doc["id"]}',json={'number':n,'designation':'Annex','title':f'Материал {n} — полное название / synthetic item'})
                    r.raise_for_status()
                r=client.patch(f'/api/projects/{pid}/submission-index',json={**DEFAULT,'enabled':True,'columns':['pages','path']});r.raise_for_status()
                started=time.perf_counter();preview=post(f'/projects/{pid}/submission-index/preview',{})
                r=client.post(f'/api/projects/{pid}/export',json={'preview_token':preview['preview_token']});r.raise_for_status()
                (out/'Submission.zip').write_bytes(r.content)
                index_documents.verify_package(r.content,preview['settings'],preview['rows'])
                with ZipFile(BytesIO(r.content)) as z:
                    for fmt in ('pdf','docx'):(out/('Exhibit index.'+fmt)).write_bytes(z.read('Submission/Exhibit index.'+fmt))
                report={'count':preview['count'],'http_seconds':time.perf_counter()-started}
                subprocess.run(['node',str(ROOT/'scripts/submission_index_ui.cjs')],cwd=ROOT,env={**os.environ,'INDEX_QA_URL':base,'INDEX_QA_OUTPUT':str(out),'EXHIBIT_BROWSER_ENTRY':browser_entry(port,Path(data))},check=True)
                with TemporaryDirectory(prefix='bl-index-scale-') as scale_data:
                    store=Store(scale_data);large=store.create('500 synthetic entries')
                    raw=make_pdf('Example',[['Synthetic input']])
                    for n in range(1,501):
                        store.upload(large,f'Annex {n}.pdf',raw,mode='passthrough')
                        store.update(large,large['documents'][-1]['id'],{'number':n,'designation':'Annex','title':f'Материал {n} — полный заголовок / synthetic material'})
                    large=index_settings.save(store,large,{**DEFAULT,'enabled':True,'columns':['pages','path']})
                    manager=submission_index.manager(store)
                    tracemalloc.start();start=time.perf_counter()
                    try:
                        with patch.object(store,'prepare',wraps=store.prepare) as prepare:
                            model=manager.preview(large['id'])
                            assert len(model['rows'])==500
                            for fmt in ('docx','pdf'):
                                value=index_documents.generate(fmt,model['settings'],model['rows'])
                                (out/('Scale index.'+fmt)).write_bytes(value)
                                assert len(set(index_documents.targets(fmt,value)))==500
                            manager.preview(large['id']);assert prepare.call_count==500
                        folders=[p['temporary'].name for p in manager.plans.values()]
                        report['scale']={'rows':500,'seconds':time.perf_counter()-start,'python_peak_bytes':tracemalloc.get_traced_memory()[1],
                                         'temporary_pdf_bytes':sum(v['bytes'] for v in manager.plans.values()),'preparations':prepare.call_count}
                    finally:
                        tracemalloc.stop();manager.close()
                    assert all(not Path(p).exists() for p in folders)
                    report['scale']['temporary_removed']=True
                (out/'report.json').write_text(json.dumps(report,indent=2),'utf-8')
            finally:
                proc.terminate()
                try:proc.wait(10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()


if __name__=='__main__':main()
