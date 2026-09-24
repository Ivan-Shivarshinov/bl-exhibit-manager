from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
import json
import stat

from exhibit import backup, history, schema, word, word_bridge
from exhibit.project import Store
from exhibit.samples import make_pdf
from exhibit.translation import Translations
from tests.test_feedback import citation_docx


class BackupTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root/'source'); self.p = self.store.create('Перенос проекта')
        self.store.upload(self.p, 'Annex 1.pdf', make_pdf('Sample', [['Sample text']]))
        self.did = self.p['documents'][0]['id']
        self.store.upload(self.p, 'Translation.pdf', make_pdf('Translation', [['Translated text']]), 'translation', self.did)
        self.store.upload(self.p, 'Main.docx', citation_docx(['Annex 1, Sample title, p. 2.']), 'main')
        self.store.scan(self.p)
        Translations(self.store).write(self.p['id'], self.did, {'status':'partial','parts':[{'source':'Sample text','translation':''}],
            'source_hash': self.p['documents'][0]['original']['sha256'], 'target':'English'})
        history.save_export(self.store, self.p, b'first immutable output')
        history.save_export(self.store, self.p, b'second immutable output')
        self.archive = self.root/'project.zip'

    def save(self):
        backup.save_archive(self.store, self.p['id'], self.archive)
        return self.archive

    def rewrite(self, transform):
        with ZipFile(self.archive) as z: parts = {n: z.read(n) for n in z.namelist()}
        transform(parts)
        with ZipFile(self.archive, 'w', ZIP_DEFLATED) as z:
            for n, value in parts.items(): z.writestr(n, value)

    def test_full_roundtrip_preserves_every_saved_byte_and_draft(self):
        # Old revisions stay in inputs, even when no longer the selected main document.
        self.store.upload(self.p, 'Main v2.docx', citation_docx(['Annex 1, Sample title, p. 3.']), 'main')
        original = {name: path.read_bytes() for name, path in backup.inventory(self.store.folder(self.p['id']))}
        self.save()
        target = Store(self.root/'target')
        check = backup.preview(target, self.archive)
        self.assertFalse(check['conflict']); self.assertEqual(check['summary']['exports'], 2)
        restored = backup.restore_archive(target, self.archive)
        self.assertEqual(restored, self.p)
        self.assertEqual(original, {n:p.read_bytes() for n,p in backup.inventory(target.folder(restored['id']))})
        self.assertEqual(Translations(target).state(restored['id'], self.did)['status'], 'partial')
        self.assertEqual(len(target.list()), 1)

    def test_ready_translated_document_needs_no_new_approval(self):
        from exhibit.project import document_ready
        self.store.update(self.p,self.did,{'number':1,'designation':'Annex','style':{'font':'DejaVu','size':10,'margin':24,'stamp_mode':'band','top':26}})
        self.store.approve(self.p,self.did,'translation'); self.store.approve(self.p,self.did,'document')
        self.assertTrue(document_ready(self.p,self.p['documents'][0]))
        self.save(); target=Store(self.root/'target'); restored=backup.restore_archive(target,self.archive)
        self.assertTrue(document_ready(restored,restored['documents'][0]))
        self.assertTrue(restored['documents'][0]['translation_confirmed'])
        self.assertEqual(self.store.prepare(self.p,self.p['documents'][0]),target.prepare(restored,restored['documents'][0]))

    def test_conflict_requires_explicit_copy_and_keeps_original(self):
        self.save(); before = (self.store.folder(self.p['id'])/'project.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'уже существует'): backup.restore_archive(self.store, self.archive)
        copied = backup.restore_archive(self.store, self.archive, copy=True)
        self.assertNotEqual(copied['id'], self.p['id'])
        self.assertEqual(before, (self.store.folder(self.p['id'])/'project.json').read_bytes())
        self.assertEqual([e['sha256'] for e in history.list_exports(self.store, self.p)], [e['sha256'] for e in history.list_exports(self.store, copied)])

    def test_corrupt_and_missing_files_rejected_before_restore(self):
        self.save()
        self.rewrite(lambda parts: parts.__setitem__('project.json', parts['project.json']+b' '))
        target = Store(self.root/'target')
        with self.assertRaisesRegex(ValueError, 'сумма'): backup.restore_archive(target, self.archive)
        self.assertEqual(target.list(), [])
        self.archive.unlink(); self.save()
        self.rewrite(lambda parts: parts.pop('inputs/'+self.p['main']['blob']))
        with self.assertRaisesRegex(ValueError, 'Состав'): backup.inspect_archive(self.archive)

    def test_complete_manifest_cannot_hide_missing_source(self):
        self.save()
        def remove(parts):
            name = 'inputs/'+self.p['main']['blob']; parts.pop(name)
            manifest = json.loads(parts['backup.json']); manifest['files'].pop(name)
            parts['backup.json'] = json.dumps(manifest).encode()
        self.rewrite(remove)
        with self.assertRaisesRegex(ValueError, 'обязательный исходник'): backup.inspect_archive(self.archive)

    def test_valid_hashes_do_not_make_incomplete_metadata_restorable(self):
        self.save()
        def incomplete(parts):
            p = json.loads(parts['project.json']); del p['documents'][0]['translation_confirmed']
            parts['project.json'] = json.dumps(p).encode()
            manifest = json.loads(parts['backup.json'])
            manifest['files']['project.json'] = backup.hash_stream(BytesIO(parts['project.json']))
            parts['backup.json'] = json.dumps(manifest).encode()
        self.rewrite(incomplete)
        target = Store(self.root/'target')
        with self.assertRaisesRegex(ValueError, 'настройки проекта'): backup.restore_archive(target, self.archive)
        self.assertEqual(target.list(), [])

    def test_bad_paths_duplicates_symlinks_and_unknown_versions(self):
        self.save(); good = self.archive.read_bytes()
        for name in ['../outside', '/absolute', 'C:/outside', 'inputs\\wrong', 'project.json', 'PROJECT.JSON']:
            with self.subTest(name=name):
                self.archive.write_bytes(good)
                with ZipFile(self.archive, 'a') as z: z.writestr(name, b'x')
                with self.assertRaises(ValueError): backup.inspect_archive(self.archive)
        self.archive.write_bytes(good)
        def version(parts):
            manifest = json.loads(parts['backup.json']); manifest['format'] = 999
            parts['backup.json'] = json.dumps(manifest).encode()
        self.rewrite(version)
        with self.assertRaisesRegex(ValueError, 'Обновите'): backup.inspect_archive(self.archive)
        self.archive.write_bytes(good)
        with ZipFile(self.archive, 'a') as z:
            info = ZipInfo('translation-'+'a'*32+'.json'); info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, b'/outside')
        with self.assertRaisesRegex(ValueError, 'путь'): backup.inspect_archive(self.archive)

    def test_limits_and_unknown_project_schema(self):
        self.save()
        with patch.object(backup, 'MAX_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'предел'): backup.inspect_archive(self.archive)
        with patch.object(backup, 'MAX_FILES', 1):
            with self.assertRaisesRegex(ValueError, 'файлов'): backup.inspect_archive(self.archive)
        self.p['schema'] = 999; self.store.save(self.p)
        before = (self.store.folder(self.p['id'])/'project.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Обновите'): self.store.load(self.p['id'])
        self.assertEqual(before, (self.store.folder(self.p['id'])/'project.json').read_bytes())

    def test_active_translation_and_unknown_files_do_not_produce_backup(self):
        with self.assertRaisesRegex(ValueError, 'идёт перевод'):
            backup.save_archive(self.store, self.p['id'], self.archive, {(self.p['id'], self.did): True})
        self.assertFalse(self.archive.exists())
        (self.store.folder(self.p['id'])/'unknown.dat').write_bytes(b'important')
        with self.assertRaisesRegex(ValueError, 'Неизвестный файл'): self.save()
        self.assertFalse(self.archive.exists())

    def test_machine_data_and_cache_excluded(self):
        (self.store.root/'private.key').write_text('secret')
        folder = self.store.folder(self.p['id'])/'main-pdf'; folder.mkdir()
        (folder/'cache.pdf').write_bytes(b'cache')
        self.save()
        with ZipFile(self.archive) as z:
            self.assertFalse(any(n.startswith('main-pdf') or 'private.key' in n for n in z.namelist()))

    def test_failed_write_is_atomic_and_retry_works(self):
        self.save(); target = Store(self.root/'target')
        with patch.object(Path, 'rename', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError, 'свободное место'): backup.restore_archive(target, self.archive)
        self.assertEqual(target.list(), []); self.assertEqual(list(target.root.iterdir()), [])
        backup.restore_archive(target, self.archive)
        self.assertEqual(len(target.list()), 1)

    def test_archive_failure_keeps_existing_destination(self):
        self.archive.write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError, 'уже существует'): self.save()
        self.assertEqual(self.archive.read_bytes(), b'existing')

    def test_schema_upgrade_backups_and_failure_rollback(self):
        before = (self.store.folder(self.p['id'])/'project.json').read_bytes()
        def migrate(p): p['schema'] = 2; p['new_setting'] = 'yes'; return p
        with patch.object(schema, 'CURRENT', 2), patch.dict(schema.UPGRADES, {1:migrate}):
            updated = self.store.load(self.p['id'])
            self.assertEqual(updated['schema'], 2)
            archives = list((self.store.root/'backups').glob('*.zip'))
            with ZipFile(archives[0]) as z: self.assertEqual(z.read('project.json'), before)
        self.store.save(self.p)
        with patch.object(schema, 'CURRENT', 2), patch.dict(schema.UPGRADES, {1:lambda p: (_ for _ in ()).throw(ValueError('bad upgrade'))}):
            with self.assertRaisesRegex(ValueError, 'Прежнее состояние'): self.store.load(self.p['id'])
        self.assertEqual((self.store.folder(self.p['id'])/'project.json').read_bytes(), before)
        with patch.object(schema, 'CURRENT', 2), patch.dict(schema.UPGRADES, {1:migrate}), patch.object(backup, 'save_archive', side_effect=OSError('full')):
            with self.assertRaisesRegex(ValueError, 'свободное место'): self.store.load(self.p['id'])
        self.assertEqual((self.store.folder(self.p['id'])/'project.json').read_bytes(), before)

    def test_failed_archive_write_removes_partial_file(self):
        original = backup.hash_stream
        def full(src, output=None):
            if output is not None:
                output.write(b'partial')
                raise OSError('disk full')
            return original(src)
        with patch.object(backup, 'hash_stream', side_effect=full):
            with self.assertRaisesRegex(ValueError, 'свободное место'): self.save()
        self.assertFalse(self.archive.exists())
        self.save()

    def test_snapshot_serializes_concurrent_project_write(self):
        from threading import Event, Thread
        from exhibit.project import LOCK
        started, done = Event(), Event()
        original = backup.hash_stream
        writers = []
        def writer():
            started.set()
            with LOCK:
                changed = self.store.load(self.p['id']); changed['name']='Changed after snapshot'
                self.store.save(changed)
            done.set()
        def observed(src, output=None):
            if output is not None and not writers:
                thread = Thread(target=writer); writers.append(thread); thread.start()
                self.assertTrue(started.wait(2)); self.assertFalse(done.is_set())
            return original(src, output)
        with patch.object(backup, 'hash_stream', side_effect=observed): self.save()
        writers[0].join(5); self.assertTrue(done.is_set())
        self.assertEqual(backup.inspect_archive(self.archive)['project']['name'],self.p['name'])
        self.assertEqual(self.store.load(self.p['id'])['name'],'Changed after snapshot')

    def test_input_size_limit_rejected_before_reading_large_input(self):
        self.save()
        with patch.object(backup,'MAX_INPUT',1):
            with self.assertRaisesRegex(ValueError,'50 МБ'): backup.inspect_archive(self.archive)

    def test_current_schema_does_not_write(self):
        with patch.object(self.store, 'save', side_effect=AssertionError('unexpected write')):
            self.assertEqual(self.store.load(self.p['id']), self.p)

    def test_managed_copy_is_bound_to_exact_source_markers(self):
        text = 'Annex 1, Sample title'
        data = word.add_links(self.store.source(self.p, self.p['main']), self.p['references'], {self.did:'Annex 1.pdf'})
        parts = word.package(data); rels = word.xml(parts[word.RELS])
        address = word_bridge.link('https://localhost:8769', self.p['id'], self.did, text)
        for rel in rels:
            if rel.get('Type','').endswith('/hyperlink'): rel.set('Target', address)
        from lxml import etree as E
        parts[word.RELS] = E.tostring(rels)
        out = BytesIO()
        with ZipFile(out, 'w') as z:
            for n,b in parts.items(): z.writestr(n,b)
        self.store.upload(self.p, 'Managed.docx', out.getvalue(), 'main')
        self.save(); copied = backup.restore_archive(self.store, self.archive, copy=True)
        self.store.scan(copied)
        self.assertEqual(copied['references'][0]['target'], self.did)
        self.assertEqual(self.store.source(copied, copied['main']), out.getvalue())
        self.assertEqual(word_bridge.catalog(copied)['restored_bindings'][0], word_bridge.parse_link(address))
        revision = BytesIO()
        with ZipFile(BytesIO(out.getvalue())) as old, ZipFile(revision, 'w') as new:
            for name in old.namelist():
                value = old.read(name)
                if name == 'word/document.xml': value = value.replace(b'</w:t>', b' Revised</w:t>', 1)
                new.writestr(name, value)
        self.store.upload(copied, 'Revised.docx', revision.getvalue(), 'main')
        self.store.scan(copied)
        self.assertEqual(copied['references'][0]['target'], self.did)
        self.store.upload(copied, 'Different.docx', citation_docx(['Other document']), 'main')
        self.assertEqual(word_bridge.catalog(copied)['restored_bindings'], [])

    def test_preview_cancel_copy_and_streamed_download(self):
        from fastapi.testclient import TestClient
        from exhibit import app as module
        with patch.object(module, 'store', self.store), patch.object(module, 'translations', Translations(self.store)):
            client = TestClient(module.app, base_url='http://localhost', headers={'X-Exhibit-Local':'1'})
            r = client.post(f"/api/projects/{self.p['id']}/backup"); self.assertEqual(r.status_code, 200, r.text)
            data = client.get(f"/api/backups/{r.json()['token']}/download").content
            r = client.post('/api/backups/preview', content=data); self.assertEqual(r.status_code, 200, r.text)
            token = r.json()['token']; self.assertTrue(r.json()['conflict'])
            self.assertEqual(client.post(f'/api/backups/{token}/restore', json={}).status_code, 400)
            r = client.post(f'/api/backups/{token}/restore', json={'copy':True}); self.assertEqual(r.status_code, 200, r.text)
            self.assertNotEqual(r.json()['id'], self.p['id'])
            token = client.post('/api/backups/preview', content=data).json()['token']
            self.assertEqual(client.post(f'/api/backups/{token}/cancel').status_code, 200)
            self.assertEqual(client.post(f'/api/backups/{token}/restore', json={}).status_code, 400)
            self.assertEqual(len(self.store.list()), 2)
