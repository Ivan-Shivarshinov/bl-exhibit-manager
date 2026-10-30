from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED
import json

from exhibit import backup, pdf
from exhibit.project import Store
from exhibit.samples import make_pdf
from tests.pdf_forms import form_pdf


class ArchiveDedupTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.store = Store(self.root/'projects')

    def archive(self, data=None, ready=False):
        p = self.store.create('Synthetic repeated references')
        data = data or make_pdf('Synthetic control', [['Control']]*20)
        self.store.upload(p, 'Sample.pdf', data, mode='passthrough' if ready else 'prepare')
        template = deepcopy(p['documents'][0]); p['documents'] = []
        for i in range(50):
            doc = deepcopy(template); doc['id'] = f'{i:032x}'; p['documents'].append(doc)
        return p, data

    def write(self, p, data):
        files = {'project.json': json.dumps(p).encode(), 'inputs/'+p['documents'][0]['original']['blob']: data}
        manifest = {n: {'size':len(v),'sha256':sha256(v).hexdigest()} for n,v in files.items()}
        path = self.root/'repeated.zip'
        with ZipFile(path, 'w', ZIP_DEFLATED) as z:
            for n,v in files.items(): z.writestr(n,v)
            z.writestr('backup.json', json.dumps({'format':1,'application':'exhibit-manager-project','files':manifest}))
        return path

    def test_one_inspection_per_blob_and_policy_and_fresh_for_every_operation(self):
        p, data = self.archive(); p['documents'][0]['translation'] = deepcopy(p['documents'][0]['original'])
        archive = self.write(p, data)
        with patch.object(pdf, 'inspect_pdf', wraps=pdf.inspect_pdf) as inspect:
            self.assertEqual(backup.inspect_archive(archive)['summary']['documents'], 50)
            self.assertEqual(inspect.call_count, 1)
            backup.inspect_archive(archive); self.assertEqual(inspect.call_count, 2)
            restored = backup.restore_archive(Store(self.root/'restored'), archive)
            self.assertEqual(inspect.call_count, 3); self.assertEqual(len(restored['documents']), 50)
        p['documents'][-1]['mode'] = 'passthrough'
        with patch.object(pdf, 'inspect_pdf', wraps=pdf.inspect_pdf) as inspect:
            backup.inspect_archive(self.write(p, data)); self.assertEqual(inspect.call_count, 2)

    def test_every_reference_checks_page_metadata_even_on_cache_hit(self):
        p, data = self.archive()
        for key, wrong in [('pages', 19), ('sizes', [])]:
            modified = deepcopy(p); modified['documents'][-1]['original'][key] = wrong
            with patch.object(pdf, 'inspect_pdf', wraps=pdf.inspect_pdf) as inspect, self.assertRaisesRegex(ValueError, 'страницах'):
                backup.inspect_archive(self.write(modified, data))
            self.assertEqual(inspect.call_count, 1)

    def test_ready_form_never_approves_strict_original_or_translation_in_either_order(self):
        p, data = self.archive(form_pdf('widget'), ready=True)
        for strict_role in ('original', 'translation'):
            for position in (0, -1):
                modified = deepcopy(p)
                if strict_role == 'original': modified['documents'][position]['mode'] = 'prepare'
                else: modified['documents'][position]['translation'] = deepcopy(modified['documents'][position]['original'])
                with self.subTest(role=strict_role, position=position), self.assertRaises(ValueError):
                    backup.inspect_archive(self.write(modified, data))

    def test_modified_archive_between_operations_is_not_trusted(self):
        p, data = self.archive(); path = self.write(p, data); backup.inspect_archive(path)
        p['documents'][-1]['original']['pages'] = 999
        self.write(p, data)
        target = Store(self.root/'restored')
        with self.assertRaises(ValueError): backup.restore_archive(target, path)
        self.assertEqual(target.list(), [])
