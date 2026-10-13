"""Library import/rename regression through the installed real office converter."""
import argparse
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote
from zipfile import ZipFile
import json
import platform
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from exhibit.project import Store
from exhibit.library import Library
from exhibit.samples import make_pdf
from exhibit import word,word_bridge,main_pdf
from pypdf import PdfReader
from tests.test_feedback import citation_docx

def verify(output):
    engine=main_pdf.available();assert engine['available'],engine
    output.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='library-office-') as tmp:
        store=Store(tmp);lib=Library(store);source=store.create('Source');store.upload(source,'Source.pdf',make_pdf('Source',[['Synthetic document']]))
        doc=source['documents'][0];store.update(source,doc['id'],{'title':'Original title','number':3,'designation':'Exhibit'});store.approve(source,doc['id'],'document')
        library=lib.create('Case');v1=lib.publish(lib.save_preview(library['id'],source['id'],[{'document_id':doc['id']}])['token'])['materials'][0]
        projects=[];old_zips=[];source_hashes=[]
        for number in (3,12):
            p=store.create('Office '+str(number));plan=lib.import_preview(p['id'],[{**v1,'number':number,'designation':'Annex','filename':f'Annex {number}.pdf','folder':'Evidence/Материалы'}])
            lib.import_apply(plan['token']);p=store.load(p['id']);did=p['documents'][0]['id'];store.approve(p,did,'document')
            original=citation_docx([f'Annex {number}, Original title, para. 1.']);store.upload(p,'Main.docx',original,'main')
            store.scan(p);assert len(p['references'])==1 and p['references'][0]['target']==did
            store.confirm_links(p);zipped=store.export(p);old_zips.append(zipped);projects.append(p);source_hashes.append(sha256(original).hexdigest())
            (output/f'Annex-{number}-before.zip').write_bytes(zipped)
        a,b=projects;before_a=(store.folder(a['id'])/'project.json').read_bytes()
        store.update(source,doc['id'],{'title':'Revised title'})
        v2=lib.publish(lib.save_preview(library['id'],source['id'],[{'document_id':doc['id'],'material_id':v1['material_id'],'allow_duplicate':True}])['token'])['materials'][0]
        plan=lib.update_preview(b['id'],b['documents'][0]['id'],v2['version_id']);lib.update_apply(plan['token']);b=store.load(b['id'])
        assert (store.folder(a['id'])/'project.json').read_bytes()==before_a
        assert not b['links_reviewed'];store.scan(b);assert b['references'][0]['target']==b['documents'][0]['id'];store.confirm_links(b)
        store.approve(b,b['documents'][0]['id'],'document');store.prepare_main_pdf(b,refresh=True);after=store.export(b);(output/'Annex-12-after.zip').write_bytes(after)
        for p,expected in zip((a,b),source_hashes):assert sha256(store.source(p,p['main'])).hexdigest()==expected
        assert word_bridge.catalog(a)['documents'][0]['title']=='Original title'
        assert word_bridge.catalog(b)['documents'][0]['title']=='Revised title'
        assert word_bridge.catalog(a)['documents'][0]['id']!=word_bridge.catalog(b)['documents'][0]['id']
        for p,zipped in ((a,old_zips[0]),(b,after)):
            target=f"Evidence/Материалы/Annex {p['documents'][0]['number']}.pdf"
            with ZipFile(BytesIO(zipped)) as z:
                package=word.package(z.read('Submission/Main document.docx'));targets={unquote(r.get('Target','')) for r in word.xml(package[word.RELS])}
                assert target in targets
                reader=PdfReader(BytesIO(z.read('Submission/Main document.pdf')))
                paths={ann.get_object()['/A']['/F']['/UF'] for page in reader.pages for ann in page.get('/Annots',[]) if ann.get_object().get('/A',{}).get('/S')=='/GoToR'}
                assert paths=={target},paths
                before=word.package(store.source(p,p['main']))
                assert package['word/document.xml']==before['word/document.xml']
            from exhibit.history import list_exports
            saved=list_exports(store,p)
            first=next(x for x in saved if x['sha256']==sha256(old_zips[0] if p['id']==a['id'] else old_zips[1]).hexdigest())
            assert (store.folder(p['id'])/'exports'/first['id']/'Submission.zip').read_bytes()==(old_zips[0] if p['id']==a['id'] else old_zips[1])
        report={'platform':platform.platform(),'converter':engine['label'],'result':'passed','docx_and_pdf_relative_paths':True,'source_docx_and_typography_preserved':True,'selected_project_catalog':True,'selective_rename':True,'old_zips_unchanged':True,
                'word_pane':'Office.js not changed; catalog verified separately. This conversion does not claim interaction with the pane.'}
        (output/'library-office.json').write_text(json.dumps(report,indent=2),'utf-8');return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('output/library-office'));args=parser.parse_args();print(json.dumps(verify(args.output)))
