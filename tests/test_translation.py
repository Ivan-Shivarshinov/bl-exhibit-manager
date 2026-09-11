import json
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import time
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from exhibit import translation_cli as cli
from exhibit.translation import Translations, source_text, draft_pdf
from exhibit.project import Store, revision
from exhibit.samples import make_pdf


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        p = self.store.create("Translation test")
        self.data = make_pdf("Учебный договор", [["1. Дата: 5 марта 2026 года. RLA-31."], ["2. Сумма: 1 250 EUR. Иван Петров."]])
        self.store.upload(p, "Original.pdf", self.data)
        self.pid, self.did = p["id"], p["documents"][0]["id"]
        self.store.update(p, self.did, {"prefix": "RLA", "number": 31})
        self.manager = Translations(self.store, lambda provider, text, target, cancel: "Translated: " + text)
        # These regression cases exercise previously saved drafts with legacy exclusions.
        self.manager.preview_source(self.pid, self.did, {"selection":[{"page":1},{"page":2}],"top":60,"bottom":60})

    def body(self, **extra):
        state = self.manager.state(self.pid, self.did)
        return {"provider": "codex", "target": "English", "reviewed_source": True,
                "token": self.manager.source(self.pid, self.did)["token"], "revision": state["revision"] if state else None, **extra}

    def start(self, **extra):
        self.manager.start(self.pid, self.did, self.body(**extra))

    def wait(self):
        deadline = time.monotonic() + 5
        while self.manager.active and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.manager.active)
        return self.manager.state(self.pid, self.did)

    def test_real_pdf_text_parts_preserve_every_character(self):
        parts = source_text(self.data)
        self.assertEqual([p["page"] for p in parts], [1, 2])
        reader = PdfReader(BytesIO(self.data))
        self.assertEqual(parts[1]["source"], reader.pages[1].extract_text().strip())
        class Page:
            def extract_text(self): return "Long paragraph " * 1500
        with patch("exhibit.translation.PdfReader", return_value=type("Reader", (), {"pages": [Page()]})()):
            chunks = source_text(b"fake")
        self.assertGreater(len(chunks), 2)
        self.assertEqual("".join(x["source"] for x in chunks), Page().extract_text().strip())
        self.assertTrue(all(len(x["source"]) <= 6000 for x in chunks))

    def test_scan_and_size_limit_never_silently_omit_pages(self):
        writer = PdfWriter(); writer.add_blank_page(595, 842); stream = BytesIO(); writer.write(stream)
        with self.assertRaisesRegex(ValueError, "OCR"):
            source_text(stream.getvalue())
        with patch("exhibit.translation.MAX_TEXT", 3), self.assertRaisesRegex(ValueError, "120 000"):
            source_text(self.data)

    def test_requires_source_consent_and_matching_token(self):
        for changes in ({"reviewed_source": False}, {"token": "old"}, {"provider": "unknown"}, {"target": ""}):
            with self.assertRaises(ValueError):
                self.manager.start(self.pid, self.did, self.body(**changes))
        self.assertIsNone(self.manager.state(self.pid, self.did))

    def test_complete_edit_confirm_pdf_and_export(self):
        self.start(); state = self.wait()
        self.assertEqual(state["status"], "complete")
        original_hash = self.store.load(self.pid)["documents"][0]["original"]["sha256"]
        texts = ["1. Agreement dated 5 March 2026. See RLA-31. <literal>", "2. Amount: EUR 1,250. Ivan Petrov."]
        state = self.manager.save(self.pid, self.did, {"revision": state["revision"], "texts": texts})
        self.assertIsNone(self.store.load(self.pid)["documents"][0]["translation"])
        result = self.manager.preview(self.pid, self.did, state["revision"])
        reader = PdfReader(BytesIO(result))
        self.assertEqual(len(reader.pages), 2)
        self.assertIn("<literal>", reader.pages[0].extract_text())
        with self.assertRaisesRegex(ValueError, "Подтвердите"):
            self.manager.apply(self.pid, self.did, {"revision": state["revision"]})
        public = self.manager.apply(self.pid, self.did, {"revision": state["revision"], "confirmed": True})
        doc = public["documents"][0]
        self.assertTrue(doc["translation_confirmed"])
        self.assertFalse(doc["ready"])
        self.assertEqual(doc["original"]["sha256"], original_hash)
        p = self.store.load(self.pid)
        self.store.approve(p, self.did, "document")
        self.assertTrue(self.store.export(p))

    def test_new_draft_does_not_change_approved_attachment(self):
        p = self.store.load(self.pid)
        self.store.upload(p, "Manual.pdf", self.data, "translation", self.did)
        self.store.approve(p, self.did, "translation"); self.store.approve(p, self.did, "document")
        before = revision(p, p["documents"][0])
        self.start(); state = self.wait()
        self.manager.save(self.pid, self.did, {"revision": state["revision"], "texts": ["Manual edit", "Other edit"]})
        p = self.store.load(self.pid)
        self.assertEqual(before, revision(p, p["documents"][0]))
        self.assertTrue(self.store.public(p)["documents"][0]["ready"])
        with self.assertRaisesRegex(ValueError, "замену черновика"):
            self.start()
        self.assertEqual(self.manager.state(self.pid, self.did)["parts"][0]["translation"], "Manual edit")

    def test_partial_failure_resume_keeps_saved_edits(self):
        calls = []
        def fake(provider, text, target, cancel):
            calls.append(text)
            if len(calls) == 2: raise ValueError("Limit")
            return "First saved"
        self.manager.translator = fake
        self.start(); state = self.wait()
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["parts"][0]["translation"], "First saved")
        state = self.manager.save(self.pid, self.did, {"revision": state["revision"], "texts": ["Human correction", ""]})
        with self.assertRaisesRegex(ValueError, "неполный"):
            self.manager.preview(self.pid, self.did, state["revision"])
        self.start(resume=True); state = self.wait()
        self.assertEqual(len(calls), 3)
        self.assertEqual(state["parts"][0]["translation"], "Human correction")
        self.assertEqual(state["status"], "complete")

    def test_cancel_and_single_job_and_edit_guard(self):
        entered = threading.Event()
        def block(provider, text, target, cancel):
            entered.set(); cancel.wait(4)
            raise cli.Cancelled()
        self.manager.translator = block
        self.start(); self.assertTrue(entered.wait(2))
        state = self.manager.state(self.pid, self.did)
        with self.assertRaisesRegex(ValueError, "Другой перевод"):
            self.start()
        with self.assertRaisesRegex(ValueError, "Дождитесь"):
            self.manager.save(self.pid, self.did, {"revision": state["revision"], "texts": ["x", "y"]})
        self.manager.cancel(self.pid, self.did); state = self.wait()
        self.assertEqual(state["status"], "cancelled")
        self.assertFalse(state["parts"][0]["translation"])

    def test_restart_marks_interrupted_preserves_parts(self):
        self.start(); self.wait()
        state = self.manager.read(self.pid, self.did)
        state["status"] = "running"; self.manager.write(self.pid, self.did, state)
        reopened = Translations(Store(self.tmp.name)).state(self.pid, self.did)
        self.assertEqual(reopened["status"], "interrupted")
        self.assertTrue(reopened["parts"][0]["translation"])

    def test_replacing_original_blocks_old_draft_preview_and_apply(self):
        self.start(); state = self.wait()
        self.store.upload(self.store.load(self.pid), "New.pdf", make_pdf("New", [["New text"]]), "original", self.did)
        self.assertTrue(self.manager.state(self.pid, self.did)["stale"])
        with self.assertRaisesRegex(ValueError, "Оригинал заменён"):
            self.manager.apply(self.pid, self.did, {"revision": state["revision"], "confirmed": True})

    def test_stale_browser_cannot_overwrite_saved_revision(self):
        self.start(); state = self.wait()
        self.manager.save(self.pid, self.did, {"revision": state["revision"], "texts": ["x", "y"]})
        with self.assertRaisesRegex(ValueError, "другом окне"):
            self.manager.save(self.pid, self.did, {"revision": state["revision"], "texts": ["wrong", "wrong"]})
        with self.assertRaisesRegex(ValueError, "другом окне"):
            self.manager.preview(self.pid, self.did, state["revision"])

    def test_invalid_text_shape_and_glyphs(self):
        self.start(); state = self.wait()
        for texts in (["one"], [3, "other"], ["\x00", "other"]):
            with self.assertRaises(ValueError):
                self.manager.save(self.pid, self.did, {"revision": state["revision"], "texts": texts})
        state["parts"][0]["translation"] = "中文"
        with self.assertRaisesRegex(ValueError, "шрифте"):
            draft_pdf(state)

    def test_http_contract_local_guard_and_preview(self):
        import exhibit.app as module
        with patch.object(module, "translations", self.manager):
            client = TestClient(module.app, base_url="http://127.0.0.1")
            base = f"/api/projects/{self.pid}/documents/{self.did}/translation"
            self.assertEqual(client.get(base).json(), None)
            self.assertEqual(client.post(base+'/start', json=self.body()).status_code, 403)
            r = client.post(base+'/start', json=self.body(), headers={"X-Exhibit-Local": "1"})
            self.assertEqual(r.status_code, 200)
            state = self.wait()
            r = client.get(base+f'/preview?revision={state["revision"]}&page=1')
            self.assertEqual(r.headers["content-type"], "image/png")
            self.assertEqual(r.headers["x-page-count"], "2")

    def test_subscription_environment_and_cli_isolation(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "never-inherit", "ANTHROPIC_API_KEY": "never-inherit", "ANTHROPIC_BASE_URL": "https://invalid.example", "CLAUDE_CODE_OAUTH_TOKEN": "never-inherit"}):
            self.assertNotIn("OPENAI_API_KEY", cli.environment())
            self.assertNotIn("ANTHROPIC_API_KEY", cli.environment())
            self.assertNotIn("ANTHROPIC_BASE_URL", cli.environment())
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", cli.environment())
        with patch.object(cli, "executable", return_value="cli"):
            args = cli.command("codex", Path(self.tmp.name))
            self.assertIn("--ignore-user-config", args)
            self.assertIn("features.shell_tool=false", args)
            self.assertIn('forced_login_method="chatgpt"', args)
            args = cli.command("claude", Path(self.tmp.name))
            self.assertIn("--safe-mode", args)
            self.assertEqual(args[args.index("--tools")+1], "")
            self.assertIn("--strict-mcp-config", args)

    def test_cli_402_and_malformed_response_never_become_translation(self):
        with patch.object(cli, "command", return_value=["cli"]), patch.object(cli, "run", return_value=(1, "API Error: 402 Insufficient Balance", "")):
            with self.assertRaisesRegex(ValueError, "402"):
                cli.translate("claude", "Text", "English", threading.Event())
        with patch.object(cli, "command", return_value=["cli"]), patch.object(cli, "run", return_value=(0, '{}', "")):
            with self.assertRaises(ValueError):
                cli.translate("claude", "Text", "English", threading.Event())

    def test_native_cli_process_cancellation_and_timeout(self):
        event = threading.Event()
        timer = threading.Timer(.3, event.set); timer.start()
        try:
            with self.assertRaises(cli.Cancelled):
                cli.run([sys.executable, "-c", "import sys,time;sys.stdin.read();time.sleep(20)"], self.tmp.name, cancel=event)
        finally:
            timer.cancel()
        with self.assertRaisesRegex(ValueError, "Ответ не получен"):
            cli.run([sys.executable, "-c", "import sys,time;sys.stdin.read();time.sleep(20)"], self.tmp.name, timeout=.3)

    def test_native_process_descendants_do_not_survive_parent_exit(self):
        marker = Path(self.tmp.name) / "orphan.txt"
        child = "import time;from pathlib import Path;time.sleep(1);Path('orphan.txt').write_text('orphan')"
        parent = f"import subprocess,sys;subprocess.Popen([sys.executable,'-c',{child!r}]);print('done')"
        code, stdout, _ = cli.run([sys.executable, "-c", parent], self.tmp.name)
        self.assertEqual(code, 0)
        self.assertIn("done", stdout)
        time.sleep(1.2)
        self.assertFalse(marker.exists())

    def test_original_changed_while_request_running_does_not_attach_response(self):
        entered, release = threading.Event(), threading.Event()
        def block(provider, text, target, cancel):
            entered.set(); release.wait(3); return "Old source translation"
        self.manager.translator = block
        self.start(); self.assertTrue(entered.wait(2))
        try:
            self.store.upload(self.store.load(self.pid), "New.pdf", make_pdf("New", [["Changed text"]]), "original", self.did)
        finally:
            release.set()
        state = self.wait()
        self.assertTrue(state["stale"])
        self.assertEqual(state["status"], "error")
        self.assertFalse(state["parts"][0]["translation"])


if __name__ == "__main__": unittest.main()
