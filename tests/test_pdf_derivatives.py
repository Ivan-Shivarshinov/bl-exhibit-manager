from io import BytesIO
import json
from tempfile import TemporaryDirectory
from unittest import TestCase
from zipfile import ZipFile

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

from exhibit.pdf_derivatives import make_derivative
from exhibit.project import Store
from tests.pdf_forms import form_pdf


def sample(*pages):
    buffer = BytesIO()
    canvas = Canvas(buffer, invariant=1)
    for text in pages:
        canvas.drawString(80, 80, text)
        canvas.linkURL('https://example.org/evidence', (60, 65, 250, 100))
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


class DerivativesTests(TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.store = Store(root.name)
        self.project = self.store.create('Искусственные исходники')
        self.first = self.upload('First.pdf', sample('First page', 'Second page'))
        self.second = self.upload('Another.pdf', sample('Another file'))

    def upload(self, name, data):
        self.store.upload(self.project, name, data, mode='passthrough')
        doc = self.project['documents'][-1]
        return {'did':doc['id'], 'part':'original', 'source':doc['original']['sha256']}

    def copy(self, operation, sources, **kwargs):
        return make_derivative(self.store, self.project['id'], operation, sources, **kwargs)

    def test_rotate_merge_and_split_preserve_sources_and_absolute_links(self):
        before = (self.store.folder(self.project['id'])/'project.json').read_bytes()
        original = [self.store.source(self.project, doc['original']) for doc in self.project['documents']]
        rotated = PdfReader(BytesIO(self.copy('rotate',[{**self.first,'pages':[2]}],angle=90)))
        self.assertEqual([page.rotation for page in rotated.pages], [0,90])
        self.assertEqual(rotated.metadata['/ExhibitManagerOperation'], 'rotate')
        merged = PdfReader(BytesIO(self.copy('merge',[{**self.first,'pages':[2,1]},{**self.second,'pages':[1]}])))
        self.assertEqual([p.extract_text().strip() for p in merged.pages], ['Second page','First page','Another file'])
        for page in merged.pages:
            self.assertEqual(str(page['/Annots'][0].get_object()['/A']['/URI']), 'https://example.org/evidence')
        self.assertEqual(json.loads(merged.metadata['/ExhibitManagerSources'])[0], {'sha256':self.first['source'],'pages':[2,1]})
        with ZipFile(BytesIO(self.copy('split',[self.first],groups=[[2],[1]]))) as archive:
            self.assertEqual(archive.namelist(), ['Part-01.pdf','Part-02.pdf'])
            self.assertEqual(PdfReader(BytesIO(archive.read('Part-01.pdf'))).pages[0].extract_text().strip(), 'Second page')
        self.assertEqual((self.store.folder(self.project['id'])/'project.json').read_bytes(), before)
        self.assertEqual([self.store.source(self.project, doc['original']) for doc in self.project['documents']],original)

    def test_invalid_partition_stale_source_forms_and_destinations(self):
        with self.assertRaisesRegex(ValueError,'ровно в одну часть'):
            self.copy('split',[self.first],groups=[[1],[1]])
        with self.assertRaisesRegex(ValueError,'Исходник изменился'):
            self.copy('rotate',[{**self.first,'source':'0'*64,'pages':[1]}],angle=90)
        for kind in ('form','signed'):
            source = self.upload(kind+'.pdf', form_pdf(kind))
            with self.assertRaisesRegex(ValueError,'формой или подписью'):
                self.copy('rotate',[{**source,'pages':[1]}],angle=90)
        with BytesIO() as data:
            writer = PdfWriter(clone_from=BytesIO(sample('Booked first','Booked second')))
            writer.add_outline_item('First',0)
            writer.write(data)
            source = self.upload('Outline.pdf',data.getvalue())
        with self.assertRaisesRegex(ValueError,'неподдержанную структуру'):
            self.copy('split',[source],groups=[[1],[2]])

    def test_generated_pdf_with_empty_outline_can_be_split(self):
        writer = PdfWriter()
        writer.append(BytesIO(sample('First page', 'Second page')))
        with BytesIO() as data:
            writer.write(data)
            source = self.upload('Empty-outline.pdf', data.getvalue())
        with ZipFile(BytesIO(self.copy('split', [source], groups=[[1], [2]]))) as archive:
            self.assertEqual(archive.namelist(), ['Part-01.pdf', 'Part-02.pdf'])
