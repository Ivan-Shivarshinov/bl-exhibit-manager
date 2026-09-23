"""Real Office export of synthetic EMF+ bitmap, TOC and relative citation links."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pypdf import PdfReader
from exhibit import main_pdf, pdf, word
from exhibit.project import Store
from exhibit.samples import make_pdf
from tests.test_office_compat import fixture


def verify(output, soffice=None):
    output.mkdir(parents=True,exist_ok=True)
    engine = {'available':True,'engine':'libreoffice','label':'LibreOffice','executable':soffice} if soffice else main_pdf.available()
    assert engine['available'], engine
    original = fixture(signature=True)
    (output/'Source.docx').write_bytes(original)
    with TemporaryDirectory() as temp, patch.object(main_pdf,'available',return_value=engine):
        store = Store(temp); project = store.create('Illustrated training')
        exhibit = make_pdf('Training',['Illustrated fixture'.split()])
        store.upload(project,'Annex 1.pdf',exhibit,mode='passthrough')
        store.update(project,project['documents'][0]['id'],{'folder':"Exhibits/Training images"})
        store.upload(project,'Main.docx',original,'main'); store.scan(project); store.confirm_links(project)
        archive = store.export(project)
        with ZipFile(BytesIO(archive)) as z:
            data = z.read('Submission/Main document.pdf')
            linked = word.package(z.read('Submission/Main document.docx'))
            assert z.read('Submission/Exhibits/Training images/Annex 1.pdf') == exhibit
        # Only footnotes/relationships may differ in the downloadable DOCX.
        for name, content in word.package(original).items():
            if name not in (word.FOOT, word.RELS): assert linked[name] == content, name
        assert store.source(project,project['main']) == original
        reader = PdfReader(BytesIO(data))
        targets = {a.get_object()['/A']['/F']['/UF'] for p in reader.pages for a in p.get('/Annots',[])
                   if a.get_object().get('/A',{}).get('/S') == '/GoToR'}
        assert targets == {'Exhibits/Training images/Annex 1.pdf'}, targets
        images = [im.image.convert('RGB') for p in reader.pages for im in p.images]
        assert images, 'The converted PDF lost its image'
        colors = [(225,30,20),(20,60,230),(30,220,70),(230,200,20)]
        def matches(im):
            points = [(im.width//4,im.height//4),(3*im.width//4,im.height//4),
                      (im.width//4,3*im.height//4),(3*im.width//4,3*im.height//4)]
            return all(max(abs(a-b) for a,b in zip(im.getpixel(pt),col)) < 20 for pt,col in zip(points,colors))
        assert any(matches(im) for im in images), 'Image colors/orientation changed'
        assert any(sum(max(abs(a-b) for a,b in zip(pixel, (22,44,99))) < 12 for pixel in im.get_flattened_data()) > 100 for im in images), 'Transparent signature image lost its ink'
        toc_colors = []
        for page in reader.pages:
            state = {'color':(0,0,0)}; stack=[]
            def operand(op,args,*_):
                if op==b'q': stack.append(state['color'])
                elif op==b'Q' and stack: state['color']=stack.pop()
                elif op==b'rg': state['color']=tuple(float(v) for v in args)
                elif op==b'g': state['color']=(float(args[0]),)*3
            def text(value,*_):
                if 'Training chapter' in value: toc_colors.append(state['color'])
            page.extract_text(visitor_operand_before=operand,visitor_text=text)
        if engine['engine']=='libreoffice': assert toc_colors and all(c==(0,0,0) for c in toc_colors),toc_colors
        (output/'Submission.zip').write_bytes(archive)
        (output/'Main.pdf').write_bytes(data)
        for i in range(len(reader.pages)):(output/f'page-{i+1}.png').write_bytes(pdf.render_png(data,i+1,1.5))
        result={'engine':engine['label'],'pages':len(reader.pages),'images':len(images),'signature_image':True,'toc_colors':toc_colors,'targets':sorted(targets),'downloaded_docx_preserved':True}
        (output/'checks.json').write_text(json.dumps(result,indent=2),encoding='utf-8'); print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',default='output/office-images'); parser.add_argument('--soffice')
    args=parser.parse_args(); verify(Path(args.output).resolve(),args.soffice)
