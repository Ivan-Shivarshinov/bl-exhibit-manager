"""Synthetic visual evidence with a real Office converter; not a link-click test."""
import argparse
from io import BytesIO
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import pypdfium2 as pdfium
from pypdf import PdfReader

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from exhibit import index_documents, index_settings, main_pdf


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    engine=main_pdf.available()
    if not engine['available']:raise RuntimeError('Real Word/LibreOffice required for this separate rendering check')
    report={'engine':{k:v for k,v in engine.items() if k!='executable'},'linkClicks':'not tested','cases':{}}
    for language in ('ru','en'):
        config={**index_settings.DEFAULT,'enabled':True,'language':language,'title':index_settings.TITLES[language],'columns':['pages','path']}
        rows=[{'id':str(n),'identifier':f'Annex {n}','title':f'Материал {n} — полное русское / English название, описание и ещё одна часть длинного названия',
               'path':f"Материалы # 50%/O'Brien & Co/Подпапка с длинным именем/Annex # {n}%.pdf",'pages':n%7+1} for n in range(1,81)]
        folder=out/language;folder.mkdir(exist_ok=True)
        for fmt in ('docx','pdf'):(folder/('Exhibit index.'+fmt)).write_bytes(index_documents.generate(fmt,config,rows))
        direct_hash=sha256((folder/'Exhibit index.pdf').read_bytes()).hexdigest()
        with TemporaryDirectory(prefix='bl-index-office-') as work:
            # LibreOffice chooses the output basename from its input. Keep the
            # real conversion separate from the directly generated PDF.
            directory=Path(work);source=directory/'Office index.docx';target=directory/'Office index.pdf'
            source.write_bytes((folder/'Exhibit index.docx').read_bytes())
            main_pdf.office_export(source,target,directory,engine)
            (folder/'Office index.pdf').write_bytes(target.read_bytes())
        assert sha256((folder/'Exhibit index.pdf').read_bytes()).hexdigest()==direct_hash
        report['cases'][language]={}
        for name in ('Exhibit index','Office index'):
            file=folder/(name+'.pdf');reader=PdfReader(file)
            text=''.join(p.extract_text().replace('\n','') for p in reader.pages)
            assert all(r['identifier'] in text for r in rows)
            assert '80' in reader.pages[-1].extract_text()
            with pdfium.PdfDocument(file) as rendered:
                for page in sorted({0,len(rendered)//2,len(rendered)-1}):
                    image=rendered[page].render(scale=1.4).to_pil();image.save(folder/(name+f'-{page+1}.png'))
            report['cases'][language][name]={'pages':len(reader.pages),'allIdentifiers':True,'lastRow':True}
    (out/'office-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')


if __name__=='__main__':main()
