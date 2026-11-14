"""Small isolated browser acceptance; artificial sources, no desktop control."""
from pathlib import Path
from tempfile import TemporaryDirectory
from io import BytesIO
from zipfile import ZipFile
import argparse,os,socket,subprocess,sys,time,json

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from exhibit.project import Store
from exhibit.samples import make_pdf
from exhibit.launch import browser_entry


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='output/qa-v070');args=parser.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='bl-volume-ui-') as data:
        store=Store(data);p=store.create('Synthetic volume acceptance')
        for n in range(1,4):
            store.upload(p,f'Annex {n}.pdf',make_pdf('Synthetic',[[f'Synthetic material {n}']]),mode='passthrough')
            store.update(p,p['documents'][-1]['id'],{'designation':'Annex','number':n,'folder':'Папка #','title':f'Материал {n}'})
        cancel_source=Path(data)/'cancel-source.pdf'
        cancel_source.write_bytes(make_pdf('Synthetic cancellation',[[f'Cancellation page {n}'] for n in range(1000)]))
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,'EXHIBIT_DATA_DIR':data,'PYTHONDONTWRITEBYTECODE':'1'}
        flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
        proc=subprocess.Popen([sys.executable,'-B','-m','exhibit.launch','--serve','--port',str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**flags)
        try:
            entry=None
            for _ in range(100):
                if proc.poll() is not None:raise RuntimeError('Isolated server exited')
                try:entry=browser_entry(port,Path(data));break
                except (ValueError,OSError):time.sleep(.1)
            if not entry:raise RuntimeError('Isolated server did not start')
            subprocess.run(['node',str(ROOT/'scripts/volumes_ui.cjs')],cwd=ROOT,env={**env,'EXHIBIT_BROWSER_ENTRY':entry,'VOLUME_QA_URL':f'http://127.0.0.1:{port}','VOLUME_QA_OUTPUT':str(out),'VOLUME_QA_PID':p['id'],'VOLUME_QA_CANCEL_PDF':str(cancel_source)},check=True,timeout=150)
            with ZipFile(out/'UI-Submission.zip') as z:
                names=z.namelist();assert len([n for n in names if n.startswith('Submission/Volumes/') and n.endswith('.pdf')])==3
                mapping=json.loads(z.read('Submission/Volumes/Volume map.json'));assert len(mapping['pages'])==3
                for d in p['documents']:assert z.read('Submission/'+d['folder']+'/'+d['filename'])==store.source(p,d['original'])
            current=store.load(p['id'])
            with ZipFile(out/'UI-Renumbered.zip') as z:
                for d in current['documents']:assert z.read('Submission/'+d['folder']+'/'+d['filename'])==store.source(current,d['original'])
            print('PASS: headless Chromium volumes/ZIP/stale/errors/ordinary export; profiles/import/versions; assigned-number swap/ZIP')
        finally:
            proc.terminate()
            try:proc.wait(timeout=15)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()


if __name__=='__main__':main()
