import tempfile
import unittest
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from pypdf import PdfReader
from fastapi.testclient import TestClient

from exhibit.project import Store, effective_format, identifier, revision, review_digest
from exhibit.samples import files, demo_project


class Batch(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.files = files()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.p = self.store.create("Массовая проверка")
        for name in ["Первый.pdf", "Второй.pdf", "Третий.pdf"]:
            self.store.upload(self.p, name, self.files["RLA-99.pdf"])
        self.ids = [d["id"] for d in self.p["documents"]]

    def tearDown(self): self.temp.cleanup()

    def apply(self, request):
        _, report = self.store.batch_plan(self.p, request)
        self.assertFalse(report["conflicts"], report)
        self.p = self.store.batch_apply(self.p, request, report["token"])
        return report

    def format(self, scope, values=None, **extra):
        return self.apply({"action":"format", "scope":scope, "values":values or {}, **extra})

    def test_numbering_preserves_existing_and_skips_occupied_in_requested_order(self):
        self.store.update(self.p, self.ids[0], {"prefix":"RLA", "number":31})
        r = self.apply({"action":"organize", "document_ids":[self.ids[2],self.ids[0],self.ids[1]],
                        "numbering":{"prefix":"RLA", "start":31}, "names":"identifier", "folder":"RLA"})
        self.assertEqual([identifier(d) for d in self.p["documents"]], ["RLA-31","RLA-33","RLA-32"])
        self.assertEqual([d["filename"] for d in self.p["documents"]], ["RLA-31.pdf","RLA-33.pdf","RLA-32.pdf"])
        self.assertEqual([c["id"] for c in r["changes"]], [self.ids[2],self.ids[0],self.ids[1]])
        self.assertEqual(self.store.load(self.p["id"]), self.p)

    def test_preview_does_not_write_and_stale_or_modified_request_is_rejected(self):
        disk = self.store.folder(self.p["id"])/"project.json"
        before, in_memory = disk.read_bytes(), deepcopy(self.p)
        req = {"action":"organize", "document_ids":self.ids, "folder":"Next"}
        _, report = self.store.batch_plan(self.p, req)
        self.assertEqual(self.p, in_memory); self.assertEqual(disk.read_bytes(), before)
        with self.assertRaisesRegex(ValueError,"изменились"):
            self.store.batch_apply(self.p, {**req,"folder":"Other"}, report["token"])
        self.store.update(self.p,self.ids[0],{"title":"Изменено после проверки"})
        with self.assertRaisesRegex(ValueError,"изменились"):
            self.store.batch_apply(self.p, req, report["token"])

    def test_failed_batch_never_partially_moves_documents(self):
        disk = self.store.folder(self.p["id"])/"project.json"
        before = disk.read_bytes()
        req={"action":"organize","document_ids":self.ids,"folder":"New","numbering":{"prefix":"bad space","start":1}}
        with self.assertRaises(ValueError): self.store.batch_plan(self.p,req)
        self.assertEqual(disk.read_bytes(),before)
        self.assertTrue(all(d["folder"]=="" for d in self.p["documents"]))

    def test_case_insensitive_path_collision_blocks_apply(self):
        self.store.update(self.p,self.ids[0],{"filename":"Same.pdf","folder":"A"})
        self.store.update(self.p,self.ids[1],{"filename":"same.PDF","folder":"B"})
        req={"action":"organize","document_ids":self.ids,"folder":"Together"}
        _,report=self.store.batch_plan(self.p,req)
        self.assertIn("duplicate_path",[c["code"] for c in report["conflicts"]])
        before=self.store.load(self.p["id"])
        with self.assertRaisesRegex(ValueError,"не применены"):self.store.batch_apply(self.p,req,report["token"])
        self.assertEqual(self.store.load(self.p["id"]),before)

    def test_manual_names_preserved_until_user_allows_generation(self):
        self.store.update(self.p,self.ids[0],{"filename":"Согласованное имя.pdf"})
        req={"action":"organize","document_ids":self.ids,"numbering":{"prefix":"BL","start":1},"names":"identifier"}
        self.apply(req)
        self.assertEqual(self.p["documents"][0]["filename"],"Согласованное имя.pdf")
        self.store.update(self.p,self.ids[0],{"filename_mode":"source"})
        self.apply(req)
        self.assertEqual(self.p["documents"][0]["filename"],"BL-1.pdf")

    def test_hierarchy_reset_and_other_groups_are_independent(self):
        for did in self.ids[:2]: self.store.update(self.p,did,{"folder":"RLA"})
        self.format("project",{"size":12,"original_label":"[Project]"})
        self.format("group",{"size":14,"original_label":"[Group]"},group="rla")
        self.format("selection",{"size":16},document_ids=[self.ids[0]])
        a,b,c=self.p["documents"]
        self.assertEqual([effective_format(self.p,d)[0]["size"] for d in [a,b,c]],[16,14,12])
        self.assertEqual(effective_format(self.p,a)[0]["original_label"],"[Group]")
        self.assertEqual(effective_format(self.p,a)[1]["size"],"document")
        self.format("selection",reset=True,document_ids=[self.ids[0]])
        self.assertEqual(effective_format(self.p,self.p["documents"][0])[0]["size"],14)
        self.format("group",reset=True,group="RLA")
        self.assertEqual(effective_format(self.p,self.p["documents"][0])[0]["size"],12)

    def test_group_changes_preserve_explicit_labels_and_can_reset_them(self):
        self.store.update(self.p,self.ids[0],{"original_label":"[Reviewed custom label]"})
        self.format("group",{"original_label":"[Group]"},group="")
        self.assertEqual(effective_format(self.p,self.p["documents"][0])[0]["original_label"],"[Reviewed custom label]")
        self.format("group",{"original_label":"[Group]"},group="",reset_overrides=True)
        self.assertEqual(effective_format(self.p,self.p["documents"][0])[0]["original_label"],"[Group]")

    def test_effective_format_reaches_pdf_and_invalidates_ready_state(self):
        self.apply({"action":"organize","document_ids":self.ids,"numbering":{"prefix":"BL","start":1}})
        for d in self.p["documents"]:self.store.approve(self.p,d["id"],"document")
        self.format("group",{"designation":"Annex","original_label":"[Group original]"},group="")
        self.assertFalse(any(d["ready"] for d in self.store.public(self.p)["documents"]))
        for d in self.p["documents"]:self.store.approve(self.p,d["id"],"document")
        data=self.store.export(self.p)
        with ZipFile(BytesIO(data)) as z:
            text=PdfReader(BytesIO(z.read("Submission/Первый.pdf"))).pages[0].extract_text()
        self.assertIn("Annex BL-1",text);self.assertIn("[Group original]",text)

    def test_long_stamps_block_batch_before_saving(self):
        req={"action":"format","scope":"project","values":{"original_label":"X"*240}}
        _,report=self.store.batch_plan(self.p,req)
        self.assertIn("stamp",[c["code"] for c in report["conflicts"]])
        with self.assertRaises(ValueError):self.store.batch_apply(self.p,req,report["token"])

    def test_legacy_approved_project_remains_approved_and_names_protected(self):
        p=demo_project(self.store)
        for d in p["documents"]:
            d.pop("format_overrides",None);d.pop("filename_mode",None)
            if d["translation"]:self.store.approve(p,d["id"],"translation")
            d["approved"]=review_digest(d,d.get("style") or p["style"])
        self.store.save(p)
        public=self.store.public(self.store.load(p["id"]))
        self.assertTrue(all(d["ready"] for d in public["documents"]))
        self.assertTrue(all(d["filename_mode"]=="manual" for d in public["documents"]))

    def test_http_batch_roundtrip_and_local_security(self):
        import exhibit.app as module
        previous=module.store;module.store=self.store
        try:
            with TestClient(module.app,base_url="http://127.0.0.1") as client:
                req={"action":"organize","document_ids":self.ids,"numbering":{"prefix":"BL","start":1},"names":"identifier"}
                url=f"/api/projects/{self.p['id']}/batch"
                self.assertEqual(client.post(url+"/preview",json=req).status_code,403)
                headers={"X-Exhibit-Local":"1"}
                preview=client.post(url+"/preview",json=req,headers=headers)
                self.assertEqual(preview.status_code,200)
                applied=client.post(url+"/apply",json={"request":req,"token":preview.json()["token"]},headers=headers)
                self.assertEqual(applied.status_code,200)
                self.assertEqual([d["identifier"] for d in applied.json()["documents"]],["BL-1","BL-2","BL-3"])
        finally:module.store=previous

    def test_folder_move_changes_inherited_style_and_requires_new_link_review(self):
        self.apply({"action":"organize","document_ids":self.ids,"numbering":{"prefix":"BL","start":1}})
        self.store.update(self.p,self.ids[0],{"folder":"Group"})
        self.format("group",{"size":14},group="Group")
        d=self.p["documents"][1]
        self.store.approve(self.p,d["id"],"document")
        self.p["links_reviewed"]=True
        report=self.apply({"action":"organize","document_ids":[d["id"]],"folder":"Group"})
        updated=self.p["documents"][1]
        self.assertEqual(effective_format(self.p,updated)[0]["size"],14)
        self.assertNotEqual(updated["approved"],revision(self.p,updated))
        self.assertTrue(report["links_need_review"])

    def test_passthrough_pdf_and_approval_survive_format_only_change(self):
        self.store.update(self.p,self.ids[0],{"mode":"passthrough"})
        self.store.approve(self.p,self.ids[0],"document")
        original=self.store.prepare(self.p,self.p["documents"][0])
        self.format("project",{"size":14})
        d=self.p["documents"][0]
        self.assertEqual(d["approved"],revision(self.p,d))
        self.assertEqual(self.store.prepare(self.p,d),original)
        self.store.style(self.p,{"font":"DejaVu","size":16,"margin":24})
        self.assertEqual(d["approved"],revision(self.p,d))


if __name__ == '__main__':unittest.main()
