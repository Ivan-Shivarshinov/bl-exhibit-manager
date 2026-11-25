from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from unittest import TestCase
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from exhibit import backup, material_text, pdf, schema, searchable_pdf
from exhibit.project import Store
from exhibit.samples import main_docx, make_pdf
from exhibit.library import Library
from exhibit.volume_jobs import manager
from exhibit import volume_settings
from exhibit.material_jobs import Jobs
from exhibit.text_index import open_index
from exhibit.translation import Translations
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
            with self.assertRaisesRegex(ValueError, 'Запустите поиск заново'):
                material_text.search_project(self.store, self.pid, 'поставки', offset=1, snapshot=first['snapshot'])

    def test_docx_locations_are_exact_and_bound_to_source(self):
        self.store.upload(self.store.load(self.pid), 'Main.docx', main_docx(), kind='main')
        docx = material_text.search_project(self.store, self.pid, '"Позиция учебной стороны"', scope='docx')['hits'][0]
        self.assertRegex(docx['label'], '^Абзац')
        note = material_text.search_project(self.store, self.pid, '"намеренно отсутствует"', scope='docx')['hits'][0]
        self.assertRegex(note['label'], '^Сноска')
        result = material_text.word_location(self.store, self.pid, note['source'], note['location'])
        self.assertEqual(result['text'][slice(*note['span'])], 'намеренно отсутствует')
        with self.assertRaisesRegex(ValueError, 'DOCX изменился'):
            material_text.word_location(self.store, self.pid, '0' * 64, note['location'])

    def test_prepared_fragment_filters_text_and_gates_volume_map(self):
        p = self.store.load(self.pid)
        self.store.update(p, self.did, {'mode': 'prepare', 'number': 1, 'selection': [{'page': 1, 'rect': [.07,.14,.64,.2]}]})
        self.store.approve(p, self.did, 'document')
        p = volume_settings.save(self.store, p, {**volume_settings.DEFAULT, 'enabled': True})
        self.assertEqual(material_text.search_project(self.store, self.pid, 'Fictional', scope='prepared')['hits'], [])
        hit, = material_text.search_project(self.store, self.pid, '"срок поставки"', scope='prepared')['hits']
        self.assertNotIn('Fictional', hit['context'])
        self.assertNotIn('volume', hit)
        service = manager(self.store)
        self.addCleanup(service.close)
        service.preview(self.pid)
        hit, = material_text.search_project(self.store, self.pid, 'поставки', scope='prepared')['hits']
        self.assertIn('volume', hit)
        self.assertTrue(service.read_volume(self.pid, hit['volume']['token'], hit['volume']['number'] - 1))
        image = material_text.prepared_image(self.store, self.pid, self.did, 'original', 1, self.source, 1, hit['rect'])
        with Image.open(BytesIO(image)) as picture:
            self.assertGreater(picture.width, picture.height * 2)
            left, top, right, bottom = material_text.union(hit['boxes'])
            area = picture.crop((int(left*picture.width), int(top*picture.height), int(right*picture.width), int(bottom*picture.height)))
            self.assertLess(area.convert('L').getextrema()[0], 128)
        self.store.update(self.store.load(self.pid), self.did, {'title': 'New title'})
        self.assertNotIn('volume', material_text.search_project(self.store, self.pid, 'поставки', scope='prepared')['hits'][0])

    def test_library_search_filters_versions_visibility_and_metadata(self):
        library = Library(self.store)
        lid = library.create('Учебное дело')['id']
        plan = library.save_preview(lid, self.pid, [{'document_id': self.did, 'metadata': {'tags': ['first']}}])
        first, = library.publish(plan['token'])['materials']
        self.store.upload(self.store.load(self.pid), 'Current.pdf', make_pdf('Current', [['Соглашение изменилось.']]), did=self.did)
        plan = library.save_preview(lid, self.pid, [{'document_id': self.did, 'material_id': first['material_id']}])
        library.publish(plan['token'])
        self.assertFalse(material_text.search_library(library, lid, 'поставки')['hits'])
        hit, = material_text.search_library(library, lid, 'поставки', history=True, tag='first')['hits']
        self.assertEqual(hit['version'], first['version_id'])
        self.assertTrue(library.preview_page(lid, hit['document'], hit['version'], hit['part'], hit['page']))
        library.hide(lid, hit['document'], True, library.load(lid)['revision'])
        self.assertFalse(material_text.search_library(library, lid, 'поставки', history=True)['hits'])
        self.assertTrue(material_text.search_library(library, lid, 'поставки', history=True, hidden=True)['hits'][0]['hidden'])

    def test_library_version_carries_only_confirmed_ocr_through_an_archive(self):
        library = Library(self.store)
        lid = library.create('Архив учебного дела')['id']
        with (patch.object(material_text, 'executable', return_value='test-engine'),
              patch.object(material_text, 'engine_languages', return_value=['eng']),
              patch.object(material_text, 'run_engine', return_value=fake_tsv())):
            draft = self.recognize()
        plan = library.save_preview(lid, self.pid, [{'document_id': self.did}])
        first, = library.publish(plan['token'])['materials']
        self.assertFalse(material_text.search_library(library, lid, 'shipment')['hits'])
        material_text.confirm(self.store, self.pid, self.did, 'original', 2, self.source, draft['revision'], ['Verified library phrase'])
        plan = library.save_preview(lid, self.pid, [{'document_id': self.did, 'material_id': first['material_id'], 'allow_duplicate': True}])
        latest, = library.publish(plan['token'])['materials']
        archive = library.archive_save(lid)
        restored_library = Library(Store(Path(self.temp.name) / 'new-profile'))
        incoming = restored_library.pending()
        (incoming / 'library.zip').write_bytes((library.pending(archive['token']) / 'library.zip').read_bytes())
        other_token = incoming.name
        restored_library.archive_preview(other_token)
        restored_library.archive_restore(other_token)
        with patch.object(material_text, 'executable', return_value=None):
            hit, = material_text.search_library(restored_library, lid, '"verified library"', history=True)['hits']
        self.assertEqual((hit['version'], hit['part'], hit['page'], hit['method']),
                         (latest['version_id'], 'ready', 2, 'confirmed-ocr'))

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

    def test_reviewed_ocr_translation_is_explicit_sliced_and_bound_to_review(self):
        p = self.store.load(self.pid)
        self.store.update(p, self.did, {'mode': 'prepare', 'number': 8})
        self.store.upload(p, 'Translation.pdf', make_pdf('Attached', [['Existing translation']]), kind='translation', did=self.did)
        attached = p['documents'][0]['translation']
        with (patch.object(material_text, 'executable', return_value='test-engine'),
              patch.object(material_text, 'engine_languages', return_value=['eng']),
              patch.object(material_text, 'run_engine', return_value=fake_tsv())):
            draft = self.recognize()
        options = {'selection': [{'page': 2, 'rect': [0, 0, .5, .2]}], 'top': 0, 'bottom': 0,
                   'placement': 'preserve', 'notes': [], 'prefer_ocr': True}
        provider = Translations(self.store)
        with self.assertRaisesRegex(ValueError, 'надёжно извлекаемого'):
            provider.source(self.pid, self.did, options)
        reviewed = material_text.confirm(self.store, self.pid, self.did, 'original', 2, self.source,
                                        draft['revision'], ['Verified shipment'])
        with self.assertRaisesRegex(ValueError, 'надёжно извлекаемого'):
            provider.source(self.pid, self.did, {**options, 'prefer_ocr': False})
        for placement in ('preserve', 'exclude'):
            result = provider.source(self.pid, self.did, {**options, 'placement': placement})
            part, = result['parts']
            self.assertEqual((part['source'], part['ocr_revision']), ('Verified shipment', reviewed['revision']))
        with self.assertRaisesRegex(ValueError, 'граница зоны пересекает текст'):
            provider.source(self.pid, self.did, {**options, 'selection':[{'page':2, 'rect':[0,0,.2,.2]}]})
        material_text.confirm(self.store, self.pid, self.did, 'original', 2, self.source, reviewed['revision'], ['Updated shipment'])
        with self.assertRaisesRegex(ValueError, 'текст изменился'):
            provider.start(self.pid, self.did, {'target':'Русский', 'provider':'codex', 'reviewed_source':True,
                                               'options':result['options'], 'token':result['token']})
        self.assertIsNone(provider.state(self.pid, self.did))
        self.assertEqual(self.store.load(self.pid)['documents'][0]['translation'], attached)

    def wait_job(self, service, token):
        deadline = monotonic() + 10
        while monotonic() < deadline:
            status = service.status(token)
            if status['state'] not in ('running', 'queued'):
                return status
            sleep(.025)
        self.fail('Text job did not finish')

    def test_index_reuses_pages_across_instances_and_can_be_cancelled(self):
        service = Jobs(Library(self.store))
        first = service.start('index_pages', {'pid':self.pid}, text_project=self.pid)['id']
        self.assertEqual(self.wait_job(service, first)['result']['reused'], 0)
        with open_index(self.store.root) as index:
            self.assertIn('Срок поставки', index.get(self.source, 1))
            self.assertEqual(index.get(self.source, 2), '')
        another = Jobs(Library(self.store))
        self.assertEqual(another.latest_text(self.pid)['id'], first)
        second = another.start('index_pages', {'pid':self.pid}, text_project=self.pid)['id']
        self.assertEqual(self.wait_job(another, second)['result']['reused'], 2)

    def test_ocr_queue_cancellation_preserves_checkpoint_and_restart_marks_interrupted(self):
        from exhibit.library import atomic_json
        service = Jobs(Library(self.store))
        args = {'pid':self.pid,'did':self.did,'part':'original','source':self.source,'pages':[1,2],'language':'eng'}
        entered = Event()
        def cancelled_engine(*args, cancel, **kwargs):
            entered.set()
            deadline = monotonic() + 8
            while not cancel() and monotonic() < deadline:
                sleep(.005)
            raise ValueError('Остановлено')
        with (patch.object(material_text,'executable',return_value='ocr'),
              patch.object(material_text,'engine_languages',return_value=['eng']),
              patch.object(material_text,'run_engine',side_effect=cancelled_engine)):
            token = service.start('ocr_pages', args, text_project=self.pid)['id']
            self.assertTrue(entered.wait(8))
            service.cancel(token)
            state = self.wait_job(service, token)
        self.assertEqual((state['state'], state['result']['embedded'], state['result']['remaining']), ('cancelled',1,1))
        atomic_json(service.root/(token+'.json'),{**state,'state':'running'})
        restarted = Jobs(Library(self.store))
        self.assertEqual(restarted.latest_text(self.pid)['state'], 'interrupted')
        with (patch.object(material_text,'executable',return_value='ocr'),
              patch.object(material_text,'engine_languages',return_value=['eng']),
              patch.object(material_text,'run_engine',return_value=fake_tsv())):
            token = restarted.start('ocr_pages', args, text_project=self.pid)['id']
            self.assertEqual(self.wait_job(restarted, token)['result']['completed'], 2)
            token = restarted.start('ocr_pages', args, text_project=self.pid)['id']
            self.assertEqual(self.wait_job(restarted, token)['result']['reused'], 1)
