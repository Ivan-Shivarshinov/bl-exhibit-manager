"""Native-package HTTP acceptance. Only stdlib/pypdf; no source runtime needed."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote
from xml.etree import ElementTree as E
from zipfile import ZipFile
import json
import shutil
import time

from pypdf import PdfReader

CONFIG={'enabled':True,'language':'ru','title':'Перечень приложений','formats':['docx','pdf'],'columns':['pages','path'],'order':'identifier'}


def check_package(raw, preview, source):
    rows=preview['rows'];paths=[r['path'] for r in rows]
    with ZipFile(BytesIO(raw)) as z:
        assert len(rows)==len(paths)==len(set(paths))
        for row in rows:
            value=z.read('Submission/'+row['path'])
            assert value==source and len(PdfReader(BytesIO(value)).pages)==row['pages']
        with ZipFile(BytesIO(z.read('Submission/Exhibit index.docx'))) as doc:
            rels=E.fromstring(doc.read('word/_rels/document.xml.rels'))
            raw_targets=[r.attrib['Target'] for r in rels if r.attrib.get('Type','').endswith('/hyperlink')]
            targets=[unquote(t) for t in raw_targets]
            assert targets==paths
            for target,path in zip(raw_targets,paths):
                assert all(c in target for c in path if ord(c)>127), 'Word file IRI must retain Unicode'
                assert '#' not in target and ' ' not in target and '?' not in target
            text=''.join(E.fromstring(doc.read('word/document.xml')).itertext())
            assert all(r['identifier'] in text and r['title'] in text for r in rows)
        pdf=PdfReader(BytesIO(z.read('Submission/Exhibit index.pdf')))
        targets=[]
        for page in pdf.pages:
            for a in page.get('/Annots',[]):
                action=a.get_object()['/A'];assert action['/S']=='/GoToR'
                spec=action['/F'].get_object()
                assert spec['/F']==spec['/UF'] and not any(k in spec for k in ('/DOS','/Unix','/Mac','/FS'))
                targets.append(str(spec['/UF']))
        assert set(targets)==set(paths)
        # Relocation, independent of localhost and of the installed app path.
        with TemporaryDirectory(prefix='bl-index-move-') as tmp:
            root=Path(tmp).resolve();z.extractall(root/'First folder')
            shutil.move(str(root/'First folder'/'Submission'),str(root/'Moved complete Submission'))
            for path in targets:
                target=(root/'Moved complete Submission'/path).resolve()
                assert target.is_relative_to(root/'Moved complete Submission') and target.read_bytes()==source


def verify(request, data, outgoing, source, incoming=None, browser=None):
    outgoing.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()
    p=request('/api/projects',{'name':'Synthetic submission index'});pid=p['id'];route='/api/projects/'+pid
    for n in range(1,101):
        p=request(route+f'/upload?name=Material{n}.pdf&mode=passthrough',raw=source)
        d=p['documents'][-1]
        p=request(route+'/documents/'+d['id'],{'number':n,'designation':'Annex','prefix':'',
            'title':f'Материал {n} — полное название / synthetic item',
            'filename':f'Annex # {n}%.pdf','folder':"Материалы # 50%/O'Brien & Co" if n%2 else ''})
    before={f.name:sha256(f.read_bytes()).hexdigest() for f in (data/pid/'inputs').iterdir()}
    legacy=request(route+'/export',{})
    p=request(route+'/submission-index',CONFIG,method='PATCH');assert p['schema']==3
    backup=data/p['schema_backup'];assert backup.is_file()
    preview=request(route+'/submission-index/preview',{});assert preview['count']==100
    assert [r['identifier'] for r in preview['rows']]==[f'Annex {n}' for n in range(1,101)]
    package=request(route+'/export',{'preview_token':preview['preview_token']});check_package(package,preview,source)
    first=request(route+'/exports')[0]['id']
    p=request(route+'/submission-index',{**CONFIG,'language':'en','title':'My exhibits'},method='PATCH')
    new=request(route+'/submission-index/preview',{})
    second=request(route+'/export',{'preview_token':new['preview_token']});check_package(second,new,source)
    assert request(route+'/exports')[0]['changes']['index_changed']
    assert request(route+'/exports/'+first)==package
    assert request(route+'/exports')[-1]['size']==len(legacy)
    token=request(route+'/backup',{})['token'];saved=request('/api/backups/'+token+'/download')
    (outgoing/'index-project.zip').write_bytes(saved)
    (outgoing/'index-model.json').write_text(json.dumps({'rows':new['rows'],'settings':new['settings']},ensure_ascii=False,indent=2),'utf-8')
    (outgoing/'index-legacy-project.zip').write_bytes(backup.read_bytes())
    check=request('/api/backups/preview',raw=saved)
    copy=request('/api/backups/'+check['token']+'/restore',{'copy':True});assert copy['id']!=pid
    assert copy['submission_index_settings']==p['submission_index_settings']
    copied='/api/projects/'+copy['id'];model=request(copied+'/submission-index/preview',{})
    check_package(request(copied+'/export',{'preview_token':model['preview_token']}),model,source)
    assert before=={f.name:sha256(f.read_bytes()).hexdigest() for f in (data/pid/'inputs').iterdir()}
    if incoming:
        for file in incoming.rglob('index-project.zip'):
            check=request('/api/backups/preview',raw=file.read_bytes())
            restored=request('/api/backups/'+check['token']+'/restore',{'copy':check['conflict']})
            route2='/api/projects/'+restored['id'];model=request(route2+'/submission-index/preview',{})
            # Compare against this archive's native preview, not against a newly
            # created local fixture with different document IDs/source bytes.
            expected=json.loads(file.with_name('index-model.json').read_text('utf-8'))
            assert model['rows']==expected['rows'] and model['settings']==expected['settings']
            raw=request(route2+'/export',{'preview_token':model['preview_token']})
            # The source fixture may differ from this runner's fixture, so inspect its own original.
            original=(data/restored['id']/'inputs'/restored['documents'][0]['original']['blob']).read_bytes()
            check_package(raw,model,original)
    (outgoing/'index-report.json').write_text(json.dumps({'count':100,'seconds':time.perf_counter()-started,
        'relocation':True,'independentRestore':True,'historyUnchanged':True,'sourceHashesUnchanged':True,'schemaBackup':True},indent=2),'utf-8')
    return pid
