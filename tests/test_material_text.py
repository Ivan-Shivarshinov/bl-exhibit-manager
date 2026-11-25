from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from exhibit import backup, material_text, pdf, schema, searchable_pdf
from exhibit.project import Store
from exhibit.samples import make_pdf
from tests.pdf_forms import form_pdf


def mixed_pdf(scan_text='Shipment adjustment received and confirmed'):
    native = make_pdf('Synthetic evidence', [['Срок поставки продлён по письму.']])
    image = pdf.render_png(make_pdf('Synthetic evidence', [[scan_text]]), 1, 2)
    raster = BytesIO()
    document = canvas.Canvas(raster, pagesize=A4, invariant=1)
    document.drawImage(ImageReader(BytesIO(image)), 0, 0, *A4)
    document.save()
    writer = PdfWriter()
    writer.append(BytesIO(native))
    writer.append(BytesIO(raster.getvalue()))
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def fake_tsv():
    return ('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
            '5\t1\t1\t1\t1\t1\t100\t200\t340\t35\t63.1\tShipment\n'
            '5\t1\t1\t1\t1\t2\t470\t200\t440\t35\t97.2\tadjustment\n')


class MaterialTextTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.original = mixed_pdf()
        self.project = self.store.create('Учебная подача')
        self.store.upload(self.project, 'Evidence.pdf', self.original, mode='passthrough')
        self.pid = self.project['id']
        self.did = self.project['documents'][0]['id']
        self.source = self.project['documents'][0]['original']['sha256']

    def recognize(self, language='eng'):
        return material_text.recognize(self.store, self.pid, self.did, 'original', 2, self.source, language)

    def test_search_embedded_text_with_viewer_coordinates(self):
        result = material_text.search_project(self.store, self.pid, '"срок поставки"')
        hit, = result['hits']
        self.assertEqual((hit['page'], hit['method'], result['without_text']), (1, 'embedded', 1))
        self.assertIn('Срок поставки', hit['context'])
        self.assertEqual(material_text.search_project(self.store, self.pid, '"срок поставки"', match_case=True)['hits'], [])
        self.assertEqual(material_text.search_project(self.store, self.pid, 'поставки письму')['hits'][0]['page'], 1)
        self.assertTrue(hit['boxes'])
        reader = PdfReader(BytesIO(self.original))
        writer = PdfWriter()
        writer.add_page(reader.pages[0].rotate(90))
        rotated = BytesIO()
        writer.write(rotated)
        self.store.upload(self.store.load(self.pid), 'Rotated.pdf', rotated.getvalue(), did=self.did)
        hit, = material_text.search_project(self.store, self.pid, '"срок поставки"')['hits']
        with Image.open(BytesIO(pdf.render_png(rotated.getvalue(), 1))) as image:
            left, top, right, bottom = hit['boxes'][0]
            ink = image.crop((int(left * image.width), int(top * image.height),
                              int(right * image.width), int(bottom * image.height))).convert('L')
            self.assertLess(ink.getextrema()[0], 128)

    def test_search_can_filter_original_and_translation(self):
        current = self.store.load(self.pid)
        self.store.upload(current, 'For translation.pdf', make_pdf('Agreement', [['Решение.']]))
        second = current['documents'][-1]
        self.store.upload(current, 'Translation.pdf', make_pdf('Translation', [['Contract deadline.']]),
                          kind='translation', did=second['id'])
        hit, = material_text.search_project(self.store, self.pid, '"contract deadline"', part='translation')['hits']
        self.assertEqual((hit['part'], hit['document'], hit['page']), ('translation', second['id'], 1))
        self.assertEqual(material_text.search_project(self.store, self.pid, 'contract', part='original')['hits'], [])

    def test_correction_archive_restore_and_corrupted_record_rejection(self):
        before = self.store.export(self.store.load(self.pid))
        with (patch.object(material_text, 'executable', return_value='test-engine'),
              patch.object(material_text, 'engine_languages', return_value=['eng']),
              patch.object(material_text, 'run_engine', return_value=fake_tsv()) as engine):
            draft = self.recognize()
            self.assertEqual(self.recognize(), draft)
            self.assertEqual(engine.call_count, 1)
        hit, = material_text.search_project(self.store, self.pid, 'shipment')['hits']
        self.assertEqual((hit['method'], hit['page']), ('ocr', 2))
        corrected = material_text.confirm(self.store, self.pid, self.did, 'original', 2, self.source,
                                          draft['revision'], ['Verified delivery date'])
        self.assertTrue(corrected['confirmed'])
        self.assertEqual(corrected['lines'][0]['box'], draft['lines'][0]['box'])
        with self.assertRaisesRegex(ValueError, 'уже изменился'):
            material_text.confirm(self.store, self.pid, self.did, 'original', 2, self.source, draft['revision'], ['Lost update'])
        current = self.store.load(self.pid)
        self.assertEqual(current['schema'], schema.CURRENT)
        self.assertEqual(backup.inspect_archive(Path(self.temp.name) / current['schema_backup'])['project']['schema'], 1)
        archive = backup.save_archive(self.store, self.pid, Path(self.temp.name) / 'reviewed.zip')
        restored_store = Store(Path(self.temp.name) / 'isolated')
        with patch.object(material_text, 'executable', return_value=None):
            restored = backup.restore_archive(restored_store, archive)
            hit, = material_text.search_project(restored_store, restored['id'], '"verified delivery"')['hits']
            self.assertEqual((hit['page'], hit['method']), (2, 'confirmed-ocr'))
            self.assertEqual(restored_store.source(restored, restored['documents'][0]['original']), self.original)
            original_id, = list((restored_store.folder(restored['id']) / 'exports').iterdir())
            self.assertEqual((original_id / 'Submission.zip').read_bytes(), before)
        corrected['lines'][0]['box'] = [0.9, 0.1, 0.3, 0.6]
        material_text.save_record(self.store, self.pid, corrected)
        failed_archive = Path(self.temp.name) / 'broken.zip'
        with self.assertRaisesRegex(ValueError, 'Повреждена запись'):
            backup.save_archive(self.store, self.pid, failed_archive)
        self.assertFalse(failed_archive.exists())

    def test_engine_missing_and_source_replaced_mid_recognition(self):
        with patch.object(material_text, 'executable', return_value=None), self.assertRaisesRegex(ValueError, 'Tesseract'):
            self.recognize()
        self.assertEqual(self.store.load(self.pid)['schema'], 1)

        def replace_source(*args, **kwargs):
            self.store.upload(self.store.load(self.pid), 'New evidence.pdf', mixed_pdf('New draft'), did=self.did)
            return fake_tsv()

        with (patch.object(material_text, 'executable', return_value='test-engine'),
              patch.object(material_text, 'engine_languages', return_value=['eng']),
              patch.object(material_text, 'run_engine', side_effect=replace_source),
              self.assertRaisesRegex(ValueError, 'Исходник изменился')):
            self.recognize()
        self.assertIsNone(material_text.read_record(self.store, self.pid, self.source, 2))
        self.assertEqual(self.store.load(self.pid)['schema'], 1)

    def test_search_continuation_rejects_changed_scope(self):
        with patch.object(material_text, 'PAGES_PER_SEARCH', 1):
            first = material_text.search_project(self.store, self.pid, 'поставки')
            self.assertEqual((first['checked'], first['next_offset']), (1, 1))
            second = material_text.search_project(self.store, self.pid, 'поставки', offset=1, snapshot=first['snapshot'])
            self.assertEqual((second['without_text'], second['next_offset']), (1, None))
            self.store.upload(self.store.load(self.pid), 'Another.pdf', mixed_pdf(), mode='passthrough')
            with self.assertRaisesRegex(ValueError, 'Состав подачи изменился'):
                material_text.search_project(self.store, self.pid, 'поставки', offset=1, snapshot=first['snapshot'])

    def test_searchable_copy_preserves_rendered_pages_and_rejects_forms_signatures(self):
        with self.assertRaisesRegex(ValueError, 'Сначала распознайте'):
            searchable_pdf.make_copy(self.store, self.pid, self.did, 'original', self.source)
        with (patch.object(material_text, 'executable', return_value='test-engine'),
              patch.object(material_text, 'engine_languages', return_value=['rus']),
              patch.object(material_text, 'run_engine', return_value=fake_tsv())):
            draft = self.recognize(language='rus')
        with self.assertRaisesRegex(ValueError, 'страницы 2'):
            searchable_pdf.make_copy(self.store, self.pid, self.did, 'original', self.source)
        material_text.confirm(self.store, self.pid, self.did, 'original', 2, self.source,
                              draft['revision'], ['Проверенная строка о поставке'])
        result = searchable_pdf.make_copy(self.store, self.pid, self.did, 'original', self.source)
        parsed = PdfReader(BytesIO(result))
        self.assertIn('Проверенная строка о поставке', parsed.pages[1].extract_text())
        self.assertEqual(parsed.metadata['/ExhibitManagerSourceSHA256'], self.source)
        for number in (1, 2):
            self.assertEqual(pdf.render_png(result, number, 1), pdf.render_png(self.original, number, 1))
        for kind in ('form', 'signed'):
            data = form_pdf(kind)
            project = self.store.create(kind)
            self.store.upload(project, f'{kind}.pdf', data, mode='passthrough')
            doc = project['documents'][0]
            with self.assertRaisesRegex(ValueError, 'формами или подписью'):
                searchable_pdf.make_copy(self.store, project['id'], doc['id'], 'original', doc['original']['sha256'])
            self.assertEqual(self.store.source(project, doc['original']), data)
