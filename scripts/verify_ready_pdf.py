"""Native API/Office acceptance with synthetic forms; optional private file stays local."""
import argparse
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from zipfile import ZipFile
from pypdf import PdfReader

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from tests.pdf_forms import form_pdf
from tests.test_feedback import citation_docx
from exhibit import pdf, word, backup, main_pdf
from exhibit.project import Store


def verify_api(base, output):
    output.mkdir(parents=True,exist_ok=True); fixtures=output/'fixtures';fixtures.mkdir(exist_ok=True)
    source=form_pdf('signed'); (fixtures/'Annex 65.pdf').write_bytes(source)
    (fixtures/'References.docx').write_bytes(citation_docx(['Annex 65, Artificial form.','Annex 65, Artificial form.','Annex 999, Missing.','Manual title without identifier.']))
    def request(path,body=None,raw=None,expected=200):
        payload=json.dumps(body).encode() if body is not None else raw
        req=Request(base+'/api'+path,data=payload,headers={'X-Exhibit-Local':'1','Content-Type':'application/json'})
        try:
            with urlopen(req,timeout=90) as r: content=r.read(); status=r.status; mime=r.headers.get('Content-Type','')
        except HTTPError as exc: content=exc.read(); status=exc.code; mime=exc.headers.get('Content-Type','')
        assert status==expected,(path,status,content[:300])
        return json.loads(content) if 'json' in mime else content
    p=request('/projects',{'name':'Native ready form'}); pid=p['id']
    p=request(f'/projects/{pid}/upload?name=Annex%2065.pdf&mode=passthrough',raw=source); did=p['documents'][0]['id']
    p=request(f'/projects/{pid}/documents/{did}',{'filename':'Renamed.pdf','folder':'Evidence/Subfolder'})
    assert request(f'/projects/{pid}/documents/{did}/pdf')==source
    for page in (1,2):
        image=request(f'/projects/{pid}/documents/{did}/preview?page={page}'); assert image.startswith(b'\x89PNG')
        (output/f'form-page-{page}.png').write_bytes(image)
    result=request(f'/projects/{pid}/export',{})
    with ZipFile(BytesIO(result)) as z: assert z.read('Submission/Evidence/Subfolder/Renamed.pdf')==source
    before=request(f'/projects/{pid}')
    error=request(f'/projects/{pid}/documents/{did}',{'mode':'prepare','title':'Must not persist'},expected=400)
    assert 'Добавить готовые PDF' in error['detail']
    assert request(f'/projects/{pid}')==before
    request(f'/projects/{pid}/upload?name=Blocked.pdf',raw=source,expected=400)
    assert request(f'/projects/{pid}')==before
    request(f'/projects/{pid}/upload?name=Broken.pdf&mode=passthrough',raw=b'not PDF',expected=400)
    assert request(f'/projects/{pid}')==before
    saved=request(f'/projects/{pid}/backup',{})
    archive=request(f"/backups/{saved['token']}/download")
    (output/'form-project.zip').write_bytes(archive)
    preview=request('/backups/preview',raw=archive)
    restored=request(f"/backups/{preview['token']}/restore",{'copy':True})
    with ZipFile(BytesIO(request(f"/projects/{restored['id']}/export",{}))) as z:
        assert z.read('Submission/Evidence/Subfolder/Renamed.pdf')==source
    # 100+ options arrive in an intentionally scrambled stored order.
    p=request('/projects',{'name':'Reference order 105'}); pid=p['id']
    numbers=[10,2,65,1,9]+[i for i in range(3,106) if i not in (9,10,65)]
    for n in numbers:
        p=request(f'/projects/{pid}/upload?name=Annex%20{n}.pdf&mode=passthrough',raw=form_pdf('empty'))
        d=p['documents'][-1]
        request(f"/projects/{pid}/documents/{d['id']}",{'number':n,'designation':'Annex'})
    for name in ('Title 10','Title 2'):
        p=request(f'/projects/{pid}/upload?name={name.replace(" ","%20")}.pdf&mode=passthrough',raw=form_pdf('empty'))
    p=request(f'/projects/{pid}/upload?kind=main&name=References.docx',raw=(fixtures/'References.docx').read_bytes())
    p=request(f'/projects/{pid}/scan',{})
    report={'result':'passed','platform':sys.platform,'pid':pid,'order':[d['id'] for d in p['documents']],
            'form_sha256':sha256(source).hexdigest(),'forms_bytes_and_archive':True,'mode_refusal_atomic':True,
            'fixture':'ephemeral certificate CMS signature; no validity assertion'}
    (output/'ready-api-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def verify_office(output, private=None):
    output.mkdir(parents=True,exist_ok=True)
    engine=main_pdf.available(); assert engine['available'],engine
    data=Path(private).read_bytes() if private else form_pdf('signed')
    expected=sha256(data).hexdigest(); pages=pdf.inspect_pdf(data,allow_forms=True)['pages']
    with TemporaryDirectory(prefix='ready-pdf-office-') as temp:
        s=Store(Path(temp)/'data'); p=s.create('Artificial ready PDF reference')
        s.upload(p,'Annex 65.pdf',data,mode='passthrough'); d=p['documents'][0]
        s.update(p,d['id'],{'designation':'Annex','number':65,'filename':'Renamed.pdf','folder':'Evidence/Subfolder'})
        source=citation_docx(['Annex 65, Artificial material, para. 2.'])
        s.upload(p,'Main.docx',source,'main'); s.scan(p); s.confirm_links(p)
        first=s.export(p)
        archive=backup.save_archive(s,p['id'],Path(temp)/'backup.zip')
        restored=backup.restore_archive(s,archive,copy=True); second=s.export(restored)
        checks=[]
        for name,package in [('first',first),('restored',second)]:
            with ZipFile(BytesIO(package)) as z:
                raw=z.read('Submission/Evidence/Subfolder/Renamed.pdf'); assert sha256(raw).hexdigest()==expected
                assert len(PdfReader(BytesIO(raw)).pages)==pages
                main=z.read('Submission/Main document.pdf')
                targets=[a.get_object()['/A'] for page in PdfReader(BytesIO(main)).pages for a in page.get('/Annots',[]) if '/A' in a.get_object()]
                assert any(a.get('/S')=='/GoToR' and str(a['/F'].get('/UF',a['/F'].get('/F')))=='Evidence/Subfolder/Renamed.pdf' for a in targets),targets
                assert b'Evidence/Subfolder/Renamed.pdf' in word.package(z.read('Submission/Main document.docx'))[word.RELS]
            checks.append({'zip':name,'pages':pages,'sha256':expected,'relative_links':True})
            (output/(name+'.zip')).write_bytes(package)
        for page in (1,pages): (output/f'page-{page}.png').write_bytes(pdf.render_png(data,page))
        assert s.source(p,p['main'])==source
        if private: assert sha256(Path(private).read_bytes()).hexdigest()==expected
    report={'result':'passed','platform':sys.platform,'engine':engine['label'],'private_file':bool(private),'checks':checks}
    (output/'ready-office-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base');parser.add_argument('--output',required=True)
    parser.add_argument('--office',action='store_true');parser.add_argument('--private-file'); args=parser.parse_args()
    report=verify_office(Path(args.output),args.private_file) if args.office else verify_api(args.base,Path(args.output))
    print(json.dumps({k:v for k,v in report.items() if k!='order'}))
