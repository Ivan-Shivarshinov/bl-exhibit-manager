"""Native Office verification of mixed Word runs and retained web citations."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from lxml import etree as E
from pypdf import PdfReader
from exhibit import word,pdf,main_pdf
from exhibit.project import Store
from exhibit.samples import make_pdf
from tests.test_reference_options import video_fixture,pack


def fixture():
    parts=word.package(video_fixture());root,rows=word.paragraphs(parts)
    run=rows[0][-1].find('w:r',word.NS);run[:]=[]
    for name,value in [('footnoteRef',None),('tab',None),('t','Annex 1, Training'),('br',None),
                       ('t','Request, para. 3; Annex 76, Training video at ')]:
        E.SubElement(run,f'{{{word.W}}}{name}').text=value
    parts[word.FOOT]=E.tostring(root)
    return pack(parts)


def verify(output):
    output.mkdir(parents=True,exist_ok=True)
    engine=main_pdf.available();assert engine['available'],engine
    source=fixture();expected={f'https://www.youtube.com/watch?v=training&t={n}' for n in (10,11)}
    checks=[]
    with TemporaryDirectory() as temp:
        store=Store(temp)
        for external_only in (False,True):
            project=store.create('Fictional web references')
            if not external_only:
                store.upload(project,'Annex 1.pdf',make_pdf('Training',[['Fictional material']]))
                doc=project['documents'][0]
                store.update(project,doc['id'],{'prefix':'','designation':'Annex','number':1})
                store.approve(project,doc['id'],'document')
            store.upload(project,'Main.docx',source,'main');store.scan(project)
            for ref in project['references']:
                if external_only or ref['mention']=='Annex 76':store.map_reference(project,ref['key'],None,keep_original=True)
            store.confirm_links(project)
            linked=store.linked_main(project)[0]
            if external_only:assert linked==source
            else:
                before=word.xml(word.package(source)[word.FOOT]);after=word.xml(word.package(linked)[word.FOOT])
                assert word.text_of(before)==word.text_of(after)
                for i in (0,1):
                    path=f'.//w:hyperlink[@r:id="rIdVideo{i}"]'
                    assert E.tostring(before.xpath(path,namespaces=word.NS)[0])==E.tostring(after.xpath(path,namespaces=word.NS)[0])
            archive=store.export(project)
            case=output/('external-only' if external_only else 'mixed');case.mkdir(exist_ok=True)
            (case/'Submission.zip').write_bytes(archive)
            with ZipFile(BytesIO(archive)) as z:
                data=z.read('Submission/Main document.pdf')
                names=z.namelist()
            actions=[a.get_object()['/A'] for page in PdfReader(BytesIO(data)).pages for a in page.get('/Annots',[]) if '/A' in a.get_object()]
            urls={str(a.get('/URI')) for a in actions if a.get('/S')=='/URI'}
            assert expected<=urls,(expected,urls)
            files=[a['/F']['/UF'] for a in actions if a.get('/S')=='/GoToR']
            # A title crossing a line break has multiple clickable PDF rectangles.
            assert set(files)==(set() if external_only else {'Annex 1.pdf'}),files
            if external_only:assert len(names)==2,names
            (case/'Main.pdf').write_bytes(data)
            for i in range(len(PdfReader(BytesIO(data)).pages)):(case/f'page-{i+1}.png').write_bytes(pdf.render_png(data,i+1,2))
            checks.append({'external_only':external_only,'original_web_urls_preserved':sorted(urls),'relative_files':files,'zip_entries':names})
    result={'engine':engine['label'],'cases':checks}
    (output/'checks.json').write_text(json.dumps(result,indent=2),'utf-8');print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='output/reference-options-office');args=parser.parse_args()
    verify(Path(args.output).resolve())
