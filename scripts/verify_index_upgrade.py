"""Real 0.5.1 -> new package -> guarded rollback in an isolated profile."""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import argparse,json,os,socket,subprocess,sys,time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request,urlopen,urlretrieve
from zipfile import ZipFile
from pypdf import PdfWriter


@contextmanager
def running(archive,root,data,slot):
    install=root/slot;install.mkdir()
    if sys.platform=='darwin':subprocess.run(['/usr/bin/ditto','-x','-k',str(archive),str(install)],check=True)
    else:
        with ZipFile(archive) as z:z.extractall(install)
    folder=next(install.iterdir());exe=folder/('BLExhibitManager.exe' if os.name=='nt' else 'BLExhibitManager.app/Contents/MacOS/BLExhibitManager')
    home=root/'profile';home.mkdir(exist_ok=True)
    env={**os.environ,'EXHIBIT_DATA_DIR':str(data),'HOME':str(home),'USERPROFILE':str(home)}
    if os.name=='nt':env.update(APPDATA=str(home/'AppData/Roaming'),LOCALAPPDATA=str(home/'AppData/Local'))
    for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','CONDA_PREFIX'):env.pop(key,None)
    env['PATH']=str(Path(os.environ['SYSTEMROOT'])/'System32') if os.name=='nt' else '/usr/bin:/bin'
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    def command(*args):
        result=subprocess.run([str(exe),'--quiet','--port',str(port),*args],cwd=root,env=env,capture_output=True,timeout=60,**flags)
        if result.returncode:
            log=data/'application.log'
            raise AssertionError(result.stderr.decode(errors='replace')+'\n'+(log.read_text('utf-8',errors='replace') if log.exists() else 'No application log'))
    def request(path,body=None,raw=None,method=None,expected=200):
        payload=json.dumps(body).encode() if body is not None else raw
        req=Request(f'http://127.0.0.1:{port}'+path,data=payload,method=method,headers={'X-Exhibit-Local':'1','Content-Type':'application/json'})
        try:
            with urlopen(req,timeout=60) as r:
                value=r.read();assert expected==r.status
                return json.loads(value) if 'json' in r.headers.get('Content-Type','') else value
        except HTTPError as exc:
            if exc.code!=expected:raise
            return json.load(exc)
    try:command('--no-browser');yield request
    finally:
        command('--stop')
        # The detached backend closes logging shortly after its health port.
        time.sleep(.5)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('package');parser.add_argument('--output',required=True);args=parser.parse_args()
    package=Path(args.package).resolve();target=package.stem.split('-0.6.0-')[-1]
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='bl-index-upgrade-') as tmp:
        root=Path(tmp);data=root/'data';old=root/f'BLExhibitManager-0.5.1-{target}.zip'
        base='https://github.com/Ivan-Shivarshinov/bl-exhibit-manager/releases/download/v0.5.1/'
        with urlopen(base+'SHA256SUMS.txt',timeout=60) as response:sums=response.read().decode()
        expected=next(line.split()[0] for line in sums.splitlines() if line.endswith(old.name))
        with urlopen(base+old.name,timeout=60) as response,old.open('wb') as destination:
            import shutil
            shutil.copyfileobj(response,destination)
        assert sha256(old.read_bytes()).hexdigest()==expected
        writer=PdfWriter();writer.add_blank_page(width=595,height=842);buffer=BytesIO();writer.write(buffer);source=buffer.getvalue()
        with running(old,root,data,'old first') as request:
            assert request('/api/health')['version']=='0.5.1'
            p=request('/api/projects',{'name':'Upgrade fixture'});pid=p['id'];route='/api/projects/'+pid
            p=request(route+'/upload?name=Annex12.pdf&mode=passthrough',raw=source)
            request(route+'/documents/'+p['documents'][0]['id'],{'designation':'Annex','number':12})
            original=request(route+'/export',{});eid=request(route+'/exports')[0]['id']
        with running(package,root,data,'new') as request:
            assert request('/api/health')['version']=='0.6.0'
            before=(data/pid/'project.json').read_bytes()
            assert request(route)['schema']==1 and (data/pid/'project.json').read_bytes()==before
            config={'enabled':True,'language':'en','title':'My exhibits','formats':['docx','pdf'],'columns':['pages','path'],'order':'identifier'}
            p=request(route+'/submission-index',config,method='PATCH');assert p['schema']==3
            recovery=(data/p['schema_backup']).read_bytes()
            preview=request(route+'/submission-index/preview',{});raw=request(route+'/export',{'preview_token':preview['preview_token']})
            with ZipFile(BytesIO(raw)) as z:assert z.read('Submission/Annex12.pdf')==source
            assert request(route+'/exports/'+eid)==original
            after=(data/pid/'project.json').read_bytes()
        with running(old,root,data,'old rollback') as request:
            error=request(route,expected=400);assert 'Версия проекта' in error['detail']
            assert (data/pid/'project.json').read_bytes()==after
            check=request('/api/backups/preview',raw=recovery)
            copy=request('/api/backups/'+check['token']+'/restore',{'copy':True});assert copy['schema']==1
            assert request('/api/projects/'+copy['id']+'/exports/'+eid)==original
        (out/'upgrade-report.json').write_text(json.dumps({'target':target,'oldVersion':'0.5.1','newVersion':'0.6.0',
            'oldPackageSha256':expected,'openingDidNotMigrate':True,'migrationAndExport':True,'oldRefusesWithoutWriting':True,'recoveryInOldApp':True},indent=2),'utf-8')


if __name__=='__main__':main()
