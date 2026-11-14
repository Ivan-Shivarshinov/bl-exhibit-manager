"""Full synthetic Annex swap -> derived DOCX -> real converter -> linked volumes."""
import argparse,json,sys,os,socket,subprocess,time
from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
from hashlib import sha256
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from exhibit import assignments,volume_settings,main_pdf,word,pdf,history,index_settings
from exhibit.project import Store
from exhibit.samples import make_pdf
from exhibit.launch import browser_entry
from exhibit.volume_jobs import manager
from tests.test_feedback import citation_docx


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);a=parser.parse_args()
    out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=True)
    engine=main_pdf.available();assert engine['available'],'Real Word/LibreOffice required'
    with TemporaryDirectory(prefix='bl-volume-office-') as tmp:
        s=Store(tmp);p=s.create('Synthetic office volumes');other=s.create('Independent submission')
        s.upload(other,'Independent.pdf',make_pdf('Independent',[['Must stay unchanged']]),mode='passthrough');other_before=deepcopy(other)
        for n in (1,2):
            s.upload(p,f'Annex {n}.pdf',make_pdf('Synthetic',[[f'Source {n} page {k}'] for k in (1,2)]))
            d=p['documents'][-1];s.update(p,d['id'],{'designation':'Annex','number':n,'folder':'Материалы # 50%'});s.approve(p,d['id'],'document')
        oldzip=s.export(p);eid=history.list_exports(s,p)[0]['id']
        original=citation_docx(['Annex 1, Synthetic first, p. 2.','Annex 1, Synthetic first, para. 4.','Annex 2, Synthetic second, p. 1.'])
        s.upload(p,'Original.docx',original,'main');s.scan(p);s.confirm_links(p);original_meta=deepcopy(p['main'])
        req={'rows':[{'id':p['documents'][0]['id'],'number':2},{'id':p['documents'][1]['id'],'number':1}],
             'naming':'identifier','references':{r['key']:'update' for r in p['references']}}
        _,_,plan=assignments.plan(s,p['id'],req);assert not plan['conflicts'],plan
        p=assignments.apply(s,p['id'],req,plan['token']);s.confirm_links(p)
        for d in p['documents']:s.approve(p,d['id'],'document')
        p=index_settings.save(s,p,{**index_settings.DEFAULT,'enabled':True,'formats':['pdf','docx']})
        p=volume_settings.save(s,p,{**volume_settings.DEFAULT,'enabled':True,'include_main':True,'max_pages':4})
        jobs=manager(s)
        try:
            first=jobs.preview(p['id']);assert len(first['references'])==3 and first['issues']
            choices={r['key']:{'document_id':r['document_id'],'revision':r['revision'],'page':(2 if n==0 else 1)} for n,r in enumerate(first['references'])}
            p=volume_settings.save(s,p,{**p['volume_settings'],'reference_pages':choices})
            plan=jobs.preview(p['id']);assert not plan['issues'],plan['issues']
            package=jobs.export_bytes(p['id'],plan['preview_token']);(out/'Submission.zip').write_bytes(package)
            with ZipFile(BytesIO(package)) as z:
                main=PdfReader(BytesIO(z.read('Submission/Main document.pdf')))
                actions=[a.get_object()['/A'] for page in main.pages for a in page.get('/Annots',[]) if a.get_object().get('/A',{}).get('/S')=='/GoToR']
                assert sorted(int(a['/D'][0]) for a in actions)==[0,0,1],actions
                text=' '.join(page.extract_text() for page in main.pages);assert 'Annex 2' in text and 'para. 4' in text
                for n in (1,2):
                    data=z.read(f'Submission/Материалы # 50%/Annex {n}.pdf')
                    assert all(f'Annex {n}' in page.extract_text() for page in PdfReader(BytesIO(data)).pages)
                for meta in plan['volumes']:
                    data=z.read('Submission/Volumes/'+meta['file']);(out/meta['file']).write_bytes(data)
                    (out/(meta['file']+'.png')).write_bytes(pdf.render_png(data,1))
            assert s.source(p,original_meta)==original and s.load(other['id'])==other_before
            assert history.read_export(s,p,eid)==oldzip
            # Separate browser exercise through the real converter on an isolated server.
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            env={**os.environ,'EXHIBIT_DATA_DIR':tmp,'PYTHONDONTWRITEBYTECODE':'1'}
            flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
            proc=subprocess.Popen([sys.executable,'-B','-m','exhibit.launch','--serve','--port',str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**flags)
            try:
                for _ in range(100):
                    try:entry=browser_entry(port,Path(tmp));break
                    except (ValueError,OSError):time.sleep(.1)
                else:raise AssertionError('Isolated office UI server did not start')
                subprocess.run(['node','scripts/volumes_main_ui.cjs'],cwd=ROOT,env={**env,'EXHIBIT_BROWSER_ENTRY':entry,
                    'VOLUME_QA_URL':f'http://127.0.0.1:{port}','VOLUME_QA_PID':p['id'],'VOLUME_QA_OUTPUT':str(out)},check=True,timeout=180)
            finally:proc.terminate();proc.wait(timeout=20)
            with ZipFile(out/'UI-main-volumes.zip') as z:
                ui_main=PdfReader(BytesIO(z.read('Submission/Main document.pdf')))
                targets=[a.get_object()['/A'] for page in ui_main.pages for a in page.get('/Annots',[]) if a.get_object().get('/A',{}).get('/S')=='/GoToR']
                assert sorted(int(a['/D'][0]) for a in targets)==[0,0,0]
                assert json.loads(z.read('Submission/Volumes/Volume map.json'))['settings']==s.load(p['id'])['volume_settings']
            assert s.source(p,original_meta)==original and s.load(other['id'])==other_before
            assert history.read_export(s,p,eid)==oldzip
            report={'passed':True,'converter':{k:v for k,v in engine.items() if k!='executable'},'source_sha256':sha256(original).hexdigest(),
                'volumes':len(plan['volumes']),'main_exact_targets':[0,0,1],'original_other_submission_old_zip_unchanged':True,
                'real_Word':'not tested','viewer_clicks':'separate CI job'}
            (out/'office-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
        finally:jobs.close()


if __name__=='__main__':main()
