from io import BytesIO
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from exhibit import pdf, word
from exhibit.project import Store, DEFAULT_STYLE
from tests.test_feedback import citation_docx
from lxml import etree as E


def long_pdf(width=1304, height=6121, occupied=False):
    stream = BytesIO()
    c = canvas.Canvas(stream, pagesize=(width, height), invariant=1)
    c.drawString(40, height-100, 'TOP-OF-LONG-PAGE')
    c.drawString(40, 50, 'BOTTOM-OF-LONG-PAGE')
    if occupied:
        c.rect(width-150, height-35, 130, 30, fill=1)
    c.save()
    return stream.getvalue()


class LongPdf(TestCase):
    def test_web_capture_sizes_upload_preview_stamp_and_export(self):
        with TemporaryDirectory() as folder:
            store = Store(folder); project = store.create('Long capture')
            for number, size in ((46, (1304, 6121)), (59, (1440, 4508))):
                data = long_pdf(*size)
                store.upload(project, f'Annex {number}.pdf', data)
                doc = project['documents'][-1]
                store.update(project, doc['id'], {'prefix':'', 'designation':'Annex', 'number':number})
                result = store.prepare(project, doc)
                info = pdf.inspect_pdf(result)
                self.assertEqual(info['sizes'], [list(size)])
                page = PdfReader(BytesIO(result)).pages[0]
                for text in ('TOP-OF-LONG-PAGE','BOTTOM-OF-LONG-PAGE',f'Annex {number}'):
                    self.assertIn(text, page.extract_text())
                image = Image.open(BytesIO(pdf.render_png(result, 1)))
                self.assertLessEqual(image.width*image.height, pdf.MAX_PREVIEW_PIXELS)
                store.approve(project, doc['id'], 'document')
            with ZipFile(BytesIO(store.export(project))) as archive:
                names = [n for n in archive.namelist() if n.endswith('.pdf')]
                self.assertEqual(len(names), 2)
                self.assertTrue(all('BOTTOM-OF-LONG-PAGE' in PdfReader(BytesIO(archive.read(n))).pages[0].extract_text() for n in names))

    def test_large_square_preview_is_bounded_and_does_not_change_pdf(self):
        data = long_pdf(10000,10000)
        image = Image.open(BytesIO(pdf.render_png(data,1)))
        self.assertLessEqual(image.width*image.height, pdf.MAX_PREVIEW_PIXELS)
        self.assertLessEqual(max(image.size), pdf.MAX_RASTER_EDGE)
        self.assertEqual(pdf.inspect_pdf(data)['sizes'],[[10000,10000]])
        for size in ((1,100), (100,15000)):
            with self.assertRaisesRegex(ValueError, 'Размер страницы'):
                pdf.inspect_pdf(long_pdf(*size))

    def test_stamp_checks_a_small_area_without_missing_collision(self):
        original = pdf.pdfium.PdfPage.render
        allocations = []
        def capture(page, *args, **kwargs):
            bitmap = original(page, *args, **kwargs)
            allocations.append(bitmap.width*bitmap.height)
            return bitmap
        with patch.object(pdf.pdfium.PdfPage, 'render', capture):
            with self.assertRaisesRegex(ValueError, 'пересекает содержимое'):
                pdf.prepare_part(long_pdf(1440,10000,True),[{'page':1}],'','Annex 59',{**DEFAULT_STYLE,'stamp_mode':'overlay'})
        self.assertTrue(allocations)
        self.assertLess(max(allocations), 100000)

    def test_fragment_renders_selected_region_without_full_page_allocation(self):
        data = long_pdf(1440,10000)
        original = pdf.pdfium.PdfPage.render
        allocations = []
        def capture(page, *args, **kwargs):
            bitmap = original(page,*args,**kwargs)
            allocations.append(bitmap.width*bitmap.height)
            return bitmap
        with patch.object(pdf.pdfium.PdfPage,'render',capture):
            result = pdf.prepare_part(data,[{'page':1,'rect':[0,.97,1,1]}],'','Annex 59',DEFAULT_STYLE)
        self.assertLess(max(allocations), 8_000_000)
        page = PdfReader(BytesIO(result)).pages[0]
        self.assertNotIn('TOP-OF-LONG-PAGE', page.extract_text())
        self.assertLess(float(page.mediabox.height),500)

    def test_unsupported_word_structure_identifies_footnote(self):
        data = citation_docx(['Annex 46, Source.'])
        parts=word.package(data);root,rows=word.paragraphs(parts)
        E.SubElement(rows[0][-1].find('w:r',word.NS),f'{{{word.W}}}drawing')
        parts[word.FOOT]=E.tostring(root);stream=BytesIO()
        with ZipFile(stream,'w') as z:
            for name,value in parts.items():z.writestr(name,value)
        data=stream.getvalue()
        refs=word.scan(data,[{'id':'a','identifier':'Annex 46','title':'Source','aliases':[]}])['references']
        with self.assertRaisesRegex(ValueError,'Сноска 1: Неподдерживаемая структура'):
            word.add_links(data,refs,{'a':'Annex 46.pdf'})
