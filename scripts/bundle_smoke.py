"""Exercise an extracted native ZIP with isolated data, cwd and no Python/Node PATH."""
from pathlib import Path
from io import BytesIO
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from zipfile import ZipFile


def main():
    archive=Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='exhibit-pilot-') as directory:
        root=Path(directory);install=root/'Installation with spaces';install.mkdir()
        if sys.platform=='darwin':subprocess.run(['/usr/bin/ditto','-x','-k',str(archive),str(install)],check=True)
        else:
            with ZipFile(archive) as z:z.extractall(install)
        folder=next(p for p in install.iterdir() if p.name.startswith('BLExhibitManager-'))
        exe=folder/('BLExhibitManager.exe' if os.name=='nt' else 'BLExhibitManager.app/Contents/MacOS/BLExhibitManager')
        data=root/'Isolated project data';cwd=root/'Empty working folder';cwd.mkdir()
        env=os.environ.copy();env['EXHIBIT_DATA_DIR']=str(data)
        for name in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','CONDA_PREFIX'):env.pop(name,None)
        env['PATH']=str(Path(os.environ['SYSTEMROOT'])/'System32') if os.name=='nt' else '/usr/bin:/bin'
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
        def command(*args):
            result=subprocess.run([str(exe),'--port',str(port),'--quiet',*args],cwd=cwd,env=env,capture_output=True,timeout=45,**flags)
            if result.returncode:
                log=data/'application.log'
                raise AssertionError(result.stderr.decode(errors='replace')+'\n'+(log.read_text('utf-8',errors='replace') if log.exists() else 'No application log'))
        def request(path,body=None,raw=None):
            payload=json.dumps(body).encode() if body is not None else raw
            req=Request(base+path,data=payload,headers={'X-Exhibit-Local':'1','Content-Type':'application/json'})
            try:
                with urlopen(req,timeout=30) as r:
                    content=r.read();return json.loads(content) if 'json' in r.headers.get('Content-Type','') else content
            except HTTPError as exc:
                time.sleep(.5) # ASGI can finish error logging after sending the 500 response.
                log=data/'application.log'
                raise AssertionError(f'{path}: HTTP {exc.code}: '+exc.read().decode(errors='replace')+'\n'+(log.read_text('utf-8',errors='replace')[-16000:] if log.exists() else 'No server log')) from exc
        try:
            command('--no-browser')
            health=request('/api/health');assert health['version']=='0.1.1'
            html=request('/').decode();assert 'root' in html
            for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"',html):assert len(request(asset))>100
            command('--no-browser') # Repeated start keeps the existing process/data.
            demo=request('/api/demo',{})
            doc=demo['documents'][0];pid=demo['id'];did=doc['id']
            assert request(f'/api/projects/{pid}/documents/{did}/preview').startswith(b'\x89PNG')
            selected=request(f'/api/projects/{pid}/documents/{did}/translation/source-preview',{})
            assert any(p.get('role')=='footer' for p in selected['parts'])
            # PDF-only flow needs no Word, provider account or installed office converter.
            project=request('/api/projects',{'name':'Bundle smoke'});pid=project['id']
            source=(data/demo['id']/'inputs'/doc['original']['blob']).read_bytes()
            project=request(f'/api/projects/{pid}/upload?name=Sample.pdf',raw=source);did=project['documents'][0]['id']
            request(f'/api/projects/{pid}/documents/{did}',{'prefix':'RLA','number':1})
            request(f'/api/projects/{pid}/documents/{did}/review/document',{})
            package=request(f'/api/projects/{pid}/export',{})
            with ZipFile(BytesIO(package)) as z:assert any(p.endswith('.pdf') for p in z.namelist())
            before={str(p.relative_to(data)):p.read_bytes() for p in data.rglob('project.json')};assert before
            command('--stop');time.sleep(.3)
            # Move the application folder as an update/install-path change, retaining external data.
            moved=root/'Moved application';shutil.move(str(folder),str(moved));folder=moved
            exe=folder/('BLExhibitManager.exe' if os.name=='nt' else 'BLExhibitManager.app/Contents/MacOS/BLExhibitManager')
            command('--no-browser');assert request(f'/api/projects/{pid}')['documents'][0]['number']==1
            after={str(p.relative_to(data)):p.read_bytes() for p in data.rglob('project.json')};assert before==after
            assert (folder/'LICENSE').is_file() and (folder/'THIRD-PARTY-NOTICES/PYTHON-LICENSE.txt').is_file()
            print(json.dumps({'bundle':archive.name,'platform':sys.platform,'version':health['version'],'checks':['isolated start','static UI','duplicate start','demo DOCX/PDF','PDFium rendering','structured extraction','PDF ZIP export','graceful stop','relocation and project persistence','licenses'],'result':'passed'}))
        finally:
            command('--stop')


if __name__=='__main__':main()
