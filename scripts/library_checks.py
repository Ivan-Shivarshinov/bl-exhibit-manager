"""Synthetic API acceptance shared by native packages; no Word/provider simulation claims."""
from pathlib import Path
from hashlib import sha256
from io import BytesIO
from statistics import quantiles
from time import perf_counter,sleep
from urllib.parse import quote
from uuid import uuid4
from copy import deepcopy
import json
import platform
import sys


def verify(request,data,output,incoming=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);data=Path(data)
    def operation(action,arguments):
        started=perf_counter();job=request('/api/material-operations',{'action':action,'arguments':arguments})
        feedback=perf_counter()-started
        assert feedback<=.3,('operation feedback',feedback)
        deadline=perf_counter()+180
        while perf_counter()<deadline:
            state=request('/api/material-operations/'+job['id'])
            if state['state']=='complete':return state['result']
            assert state['state'] not in ('error','cancelled','interrupted'),state
            sleep(.05)
        raise AssertionError('Library operation did not finish')
    demo=request('/api/demo',{})
    sample=next(d for d in demo['documents'] if d['number']==31)
    original=(data/demo['id']/'inputs'/sample['original']['blob']).read_bytes()
    translation=(data/demo['id']/'inputs'/sample['translation']['blob']).read_bytes()
    source=request('/api/projects',{'name':'Library source'})
    source=request(f"/api/projects/{source['id']}/upload?name=Original.pdf",raw=original);sid=source['documents'][0]['id']
    source=request(f"/api/projects/{source['id']}/upload?kind=translation&did={sid}&name=Translation.pdf",raw=translation)
    request(f"/api/projects/{source['id']}/documents/{sid}",{'number':3,'designation':'Annex','title':'Material one'})
    request(f"/api/projects/{source['id']}/documents/{sid}/review/translation",{})
    source=request(f"/api/projects/{source['id']}/documents/{sid}/review/document",{})
    request(f"/api/projects/{source['id']}/export",{})
    lib=request('/api/libraries',{'name':'Case one'});other=request('/api/libraries',{'name':'Case two'})
    preview=operation('save_preview',{'lid':lib['id'],'pid':source['id'],'rows':[{'document_id':sid}]})
    material=operation('publish',{'token':preview['token']})['materials'][0]
    versions=[material];upload_rows=[]
    for i in range(19):
        uploaded=request('/api/library-files?name='+quote(f'Material {i+2}.pdf'),raw=original)
        upload_rows.append({'token':uploaded['token'],'role':'ready','allow_duplicate':True,'metadata':{'title':f'Material {i+2:02d}','language':'Русский','category':'Evidence','tags':['exchange'],'document_date':'2026-10-09'}})
    preview=operation('upload_preview',{'lid':lib['id'],'rows':upload_rows});assert not preview['errors']
    versions+=operation('publish',{'token':preview['token']})['materials']
    other_preview=operation('save_preview',{'lid':other['id'],'pid':source['id'],'rows':[{'document_id':sid}]})
    operation('publish',{'token':other_preview['token']})
    projects=[]
    for number in (3,12):
        p=request('/api/projects',{'name':'Submission '+str(number)})
        rows=[{**m,'number':number if i==0 else 100+i,'designation':'Annex','filename':f'Annex {number if i==0 else 100+i}.pdf','folder':'Evidence','mode':'prepare' if i==0 else 'passthrough'} for i,m in enumerate(versions)]
        plan=operation('import_preview',{'pid':p['id'],'rows':rows});assert not plan['errors']
        p=operation('import_apply',{'token':plan['token']});did=p['documents'][0]['id']
        p=request(f"/api/projects/{p['id']}/documents/{did}/review/document",{})
        zipped=request(f"/api/projects/{p['id']}/export",{});(output/f'Submission-{number}.zip').write_bytes(zipped)
        projects.append(p)
    a,b=projects
    def tree(pid):return {p.relative_to(data/pid).as_posix():sha256(p.read_bytes()).hexdigest() for p in (data/pid).rglob('*') if p.is_file()}
    old_a,old_b=tree(a['id']),tree(b['id'])
    source=request(f"/api/projects/{source['id']}/documents/{sid}",{'title':'Material one, revised title'})
    plan=operation('save_preview',{'lid':lib['id'],'pid':source['id'],'rows':[{'document_id':sid,'material_id':material['material_id'],'allow_duplicate':True,'comment':'Title corrected'}]})
    v2=operation('publish',{'token':plan['token']})['materials'][0]
    assert tree(a['id'])==old_a and tree(b['id'])==old_b
    comparison=operation('compare',{'lid':lib['id'],'mid':material['material_id'],'before':material['version_id'],'after':v2['version_id']})
    assert next(r['changed'] for r in comparison['changes'] if r['field']=='title')
    did=b['documents'][0]['id'];plan=operation('update_preview',{'pid':b['id'],'did':did,'version_id':v2['version_id']})
    b=operation('update_apply',{'token':plan['token']});assert b['documents'][0]['number']==12
    assert tree(a['id'])==old_a
    assert all(tree(b['id'])[n]==h for n,h in old_b.items() if n!='project.json')
    pzip=request(f"/api/projects/{b['id']}/backup",{})['token'];(output/'library-project.zip').write_bytes(request('/api/backups/'+pzip+'/download'))
    archived=operation('archive_save',{'lid':lib['id']});(output/'library.zip').write_bytes(request('/api/library-archives/'+archived['token']+'/download'))
    # Absence of the library never disables existing project bytes or ZIP assembly.
    (data/'libraries'/lib['id']).rename(data/'libraries'/(lib['id']+'-unavailable'))
    try:
        assert not request(f"/api/projects/{b['id']}/library-status")[0]['available']
        request(f"/api/projects/{b['id']}/documents/{did}/review/document",{})
        request(f"/api/projects/{b['id']}/export",{})
    finally:(data/'libraries'/(lib['id']+'-unavailable')).rename(data/'libraries'/lib['id'])
    plan=operation('update_preview',{'pid':b['id'],'did':did,'version_id':material['version_id']})
    b=operation('update_apply',{'token':plan['token']});assert b['documents'][0]['title']=='Material one'
    assert tree(a['id'])==old_a
    exchanged=[]
    if incoming:
        for archive in sorted(Path(incoming).rglob('library.zip')):
            uploaded=request('/api/library-files?archive=true&name=library.zip',raw=archive.read_bytes())
            preview=operation('archive_preview',{'token':uploaded['token']})
            restored=operation('archive_restore',{'token':uploaded['token'],'copy':preview['conflict']})
            assert restored['summary']['materials']==20 and restored['summary']['versions']==21
            exchanged.append(str(archive))
        for archive in sorted(Path(incoming).rglob('library-project.zip')):
            preview=request('/api/backups/preview',raw=archive.read_bytes());restored=request(f"/api/backups/{preview['token']}/restore",{'copy':preview['conflict']})
            with __import__('zipfile').ZipFile(BytesIO(archive.read_bytes())) as z:
                for name in z.namelist():
                    if name.startswith(('inputs/','exports/')):assert (data/restored['id']/name).read_bytes()==z.read(name)
            first=restored['documents'][0];request(f"/api/projects/{restored['id']}/documents/{first['id']}/review/document",{});request(f"/api/projects/{restored['id']}/export",{})
            exchanged.append(str(archive))
        for archive in sorted(Path(incoming).rglob('legacy-project.zip')):
            preview=request('/api/backups/preview',raw=archive.read_bytes());restored=request(f"/api/backups/{preview['token']}/restore",{'copy':preview['conflict']})
            assert restored['schema']==1
            with __import__('zipfile').ZipFile(BytesIO(archive.read_bytes())) as z:
                for name in z.namelist():
                    if name.startswith(('inputs/','exports/')):assert (data/restored['id']/name).read_bytes()==z.read(name)
            request(f"/api/projects/{restored['id']}/export",{})
            exchanged.append(str(archive))
    report={'platform':platform.platform(),'python':platform.python_version(),'result':'passed','materials':20,'libraries':2,'numbers':[3,12],
            'selective_update_and_rollback':True,'source_and_old_zip_hashes_preserved':True,'exchange':exchanged}
    (output/'library-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    return report


def load_check(request,data,output):
    """Validated immutable fixture; measure production catalog APIs, not PDF content search."""
    from exhibit.library import Library,check_version,atomic_json
    from exhibit.library_backup import summary
    from exhibit.project import Store
    from exhibit.samples import make_pdf
    libstore=Library(Store(data));lib=libstore.create('Load 1000 / 3000');lid=lib['id'];folder=libstore.folder(lid)
    upload=libstore.pending();part=libstore.stage_part(upload,'Synthetic load.pdf',make_pdf('Load',[['A synthetic source']]))
    (folder/'blobs').mkdir();(folder/'versions').mkdir();__import__('shutil').copyfile(upload/part['blob'],folder/'blobs'/part['blob'])
    from exhibit.project import DEFAULT_STYLE,DEFAULT_LABELS,NEW_LAYOUT
    for i in range(1000):
        mid=uuid4().hex;versions=[];parent=None
        for n in range(3):
            vid=uuid4().hex;v={'schema':1,'id':vid,'library_id':lid,'material_id':mid,'parent':parent,'created_at':f'2026-10-0{n+1}T12:00:00+00:00','comment':'Synthetic load fixture',
                'metadata':{'title':f'Материал {i:04d}','short_title':f'Case {i}','language':'Русский','category':'Evidence','document_date':'2026-10-09','tags':['large']},
                'parts':{'ready':part},'recipe':{'selection':[],'translation_selection':[],'format':{**DEFAULT_STYLE,**DEFAULT_LABELS,**NEW_LAYOUT}},'translation_binding':None,'ready_identifier':None}
            check_version(v);atomic_json(folder/'versions'/(vid+'.json'),v);versions.append(vid);parent=vid
        lib['materials'][mid]={'id':mid,'hidden':False,'versions':versions,'latest':summary(v)}
    atomic_json(folder/'library.json',lib)
    def timed(path):
        start=perf_counter();r=request(path);return perf_counter()-start,r
    first,result=timed(f'/api/libraries/{lid}/materials');assert result['total']==1000 and len(result['items'])==50
    measurements=[timed(f'/api/libraries/{lid}/materials?q='+quote(f'Материал {i%10}'))[0] for i in range(40)]
    p95=quantiles(measurements,n=100)[94];assert first<=2 and p95<=1,('catalog budget',first,p95)
    # A 22 MB raster document exercises bounded raster preview without text/OCR.
    from PIL import Image
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    import random
    image=Image.frombytes('RGB',(2100,3500),random.Random(17).randbytes(2100*3500*3))
    stream=BytesIO();c=canvas.Canvas(stream,pagesize=(1200,2000));c.drawImage(ImageReader(image),0,0,1200,2000);c.save();scan=stream.getvalue()
    uploaded=request('/api/library-files?name=Large%20scan.pdf',raw=scan)
    assert request('/api/library-files/'+uploaded['token']+'/preview').startswith(b'\x89PNG')
    rows=[]
    for i in range(100):rows.append({'token':request('/api/library-files?name='+quote(f'Batch {i}.pdf'),raw=make_pdf('Batch '+str(i),[['Row '+str(i)]]))['token'],'role':'ready','metadata':{'title':f'Batch {i}'}})
    def op(action,args):
        start=perf_counter();job=request('/api/material-operations',{'action':action,'arguments':args});feedback=perf_counter()-start;assert feedback<=.3
        while True:
            state=request('/api/material-operations/'+job['id'])
            if state['state']=='complete':return state['result'],feedback
            assert state['state'] not in ('error','cancelled','interrupted'),state;sleep(.05)
    preview,feedback=op('upload_preview',{'lid':lid,'rows':rows});assert not preview['errors']
    published,_=op('publish',{'token':preview['token']});assert len(published['materials'])==100
    archived,_=op('archive_save',{'lid':lid});assert archived['summary']['versions']==3100
    checked,_=op('archive_preview',{'token':archived['token']});assert checked['conflict']
    restored,_=op('archive_restore',{'token':archived['token'],'copy':True})
    assert restored['id']!=lid and restored['summary']==archived['summary']
    assert request(f"/api/libraries/{restored['id']}/materials")['total']==1100
    report={'platform':platform.platform(),'catalog_materials':1000,'versions':3000,'first_page_seconds':first,'warm_search_p95_seconds':p95,'operation_feedback_seconds':feedback,'scan_bytes':len(scan),'bulk_pdf_count':100,'archive_restored_versions':3100,'result':'passed'}
    Path(output).mkdir(parents=True,exist_ok=True);(Path(output)/'library-load.json').write_text(json.dumps(report,indent=2),'utf-8')
    return report
