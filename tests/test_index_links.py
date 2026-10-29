"""File IRIs in Word and literal PDF Filespecs are different contracts."""
import ctypes
from io import BytesIO
from unittest import TestCase
from urllib.parse import quote
from xml.etree import ElementTree as E
from zipfile import ZipFile

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject
import pypdfium2 as pdfium
from pypdfium2 import raw

from exhibit import index_documents as index, index_settings
from exhibit.samples import make_pdf


CASES = [
    ('Annex 12.pdf', 'Annex%2012.pdf'),
    ('Материалы/Annex 13.pdf', 'Материалы/Annex%2013.pdf'),
    ("Материалы # 50%/O'Brien & Co/Annex 12%.pdf", "Материалы%20%23%2050%25/O%27Brien%20%26%20Co/Annex%2012%25.pdf"),
    ('Литерал %23/Annex # 13.pdf', 'Литерал%20%2523/Annex%20%23%2013.pdf'),
]


def replace_part(data, name, value):
    out=BytesIO()
    with ZipFile(BytesIO(data)) as src, ZipFile(out,'w') as dst:
        for info in src.infolist():dst.writestr(info,value if info.filename==name else src.read(info))
    return out.getvalue()


class IndexLinkTests(TestCase):
    def setUp(self):
        self.config={**index_settings.DEFAULT,'enabled':True,'formats':['docx','pdf'],'columns':[]}
        self.rows=[{'path':path,'identifier':f'Annex {12+n}','title':f'Target {12+n}','pages':1} for n,(path,_) in enumerate(CASES)]
        self.original=make_pdf('Synthetic target',[['One page']])

    def package(self, docx=None, pdf=None):
        out=BytesIO()
        with ZipFile(out,'w') as z:
            for row in self.rows:z.writestr('Submission/'+row['path'],self.original)
            for fmt,value in (('docx',docx),('pdf',pdf)):
                z.writestr('Submission/Exhibit index.'+fmt,value if value is not None else index.generate(fmt,self.config,self.rows))
        return out.getvalue()

    def test_word_iris_keep_unicode_and_escape_literal_delimiters_once(self):
        data=index.generate('docx',self.config,self.rows)
        with ZipFile(BytesIO(data)) as z:
            rels=E.fromstring(z.read('word/_rels/document.xml.rels'))
        links=[r for r in rels if r.get('Type','').endswith('/hyperlink')]
        self.assertEqual([r.get('Target') for r in links],[target for _,target in CASES])
        self.assertTrue(all(r.get('TargetMode')=='External' for r in links))
        self.assertEqual(index.targets('docx',data),[path for path,_ in CASES])
        index.verify_package(self.package(docx=data),self.config,self.rows)

    def test_package_rejects_ascii_only_unicode_uri_even_when_decoded_target_matches(self):
        data=index.generate('docx',self.config,self.rows)
        with ZipFile(BytesIO(data)) as z:
            rels=E.fromstring(z.read('word/_rels/document.xml.rels'))
        for r in rels:
            if r.get('Target')==CASES[1][1]:r.set('Target',quote(CASES[1][0],safe='/'))
        bad=replace_part(data,'word/_rels/document.xml.rels',E.tostring(rels,encoding='utf-8'))
        # Merely unquoting the paths used to accept this broken Word case.
        self.assertEqual(index.targets('docx',bad),index.targets('docx',data))
        with self.assertRaisesRegex(ValueError,'цель'):
            index.verify_package(self.package(docx=bad),self.config,self.rows)

    def test_native_pdfium_reads_exact_literal_file_paths(self):
        data=index.generate('pdf',self.config,self.rows)
        reader=PdfReader(BytesIO(data))
        for ref,(path,_) in zip(reader.pages[0]['/Annots'],CASES):
            spec=ref.get_object()['/A']['/F']
            self.assertEqual(spec['/F'],path);self.assertEqual(spec['/UF'],path)
        # Independent native PDF engine, rather than our own unquote round-trip.
        found=[]
        with pdfium.PdfDocument(data) as doc:
            page=doc[0]
            try:
                position=ctypes.c_int(0);link=raw.FPDF_LINK()
                while raw.FPDFLink_Enumerate(page,ctypes.byref(position),ctypes.byref(link)):
                    action=raw.FPDFLink_GetAction(link)
                    self.assertEqual(raw.FPDFAction_GetType(action),raw.PDFACTION_REMOTEGOTO)
                    size=raw.FPDFAction_GetFilePath(action,None,0)
                    buffer=ctypes.create_string_buffer(size)
                    self.assertEqual(raw.FPDFAction_GetFilePath(action,buffer,size),size)
                    found.append(buffer.value.decode('utf-8').replace('\\','/'))
            finally:page.close()
        self.assertEqual(found,[path for path,_ in CASES])

    def test_package_rejects_conflicting_pdf_filename_fields(self):
        data=index.generate('pdf',self.config,self.rows)
        writer=PdfWriter();writer.append(PdfReader(BytesIO(data)))
        spec=writer.pages[0]['/Annots'][2].get_object()['/A']['/F']
        spec[NameObject('/F')]=TextStringObject(quote(CASES[2][0],safe='/'))
        out=BytesIO();writer.write(out)
        with self.assertRaisesRegex(ValueError,'имена файла'):
            index.verify_package(self.package(pdf=out.getvalue()),self.config,self.rows)
