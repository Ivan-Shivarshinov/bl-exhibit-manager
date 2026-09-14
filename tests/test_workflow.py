import json
import os
import tempfile
import unittest
from unittest.mock import patch
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from urllib.parse import unquote

from lxml import etree as E
from pypdf import PdfReader, PdfWriter
from fastapi.testclient import TestClient

from exhibit import pdf, word
from exhibit.project import Store, identifier, safe_path, review_digest
from exhibit.samples import demo_project, files, make_pdf


class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = files()

    def setUp(self):
        office = patch.object(Store, "prepare_main_pdf", return_value=self.files["RLA-99.pdf"])
        office.start(); self.addCleanup(office.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.p = demo_project(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def ready(self, with_missing=True):
        p = self.p
        if with_missing:
            self.store.upload(p, "RLA-99.pdf", self.files["RLA-99.pdf"])
            self.store.update(p, p["documents"][-1]["id"], {"prefix": "RLA", "number": 99, "folder": "RLA"})
        for d in p["documents"]:
            if d["translation"]:
                self.store.approve(p, d["id"], "translation")
            self.store.approve(p, d["id"], "document")
        if p["main"]:
            self.store.scan(p)
            if with_missing:
                self.store.confirm_links(p)
        return p

    def test_full_workflow_relative_links_originals_and_package_preservation(self):
        p = self.ready()
        before = {f.name: f.read_bytes() for f in self.store.folder(p["id"]).glob("inputs/*")}
        data = self.store.export(p)
        with ZipFile(BytesIO(data)) as z:
            self.assertEqual(len(z.namelist()), 7)
            self.assertEqual(z.read("Submission/R/R-1.pdf"), self.files["R-1.pdf"])
            self.assertEqual(z.read("Submission/Other documents/Statement of Claim.pdf"), self.files["Statement of Claim.pdf"])
            main = word.package(z.read("Submission/Main document.docx"))
            original = word.package(self.files["Main document.docx"])
            for name in original:
                if name not in (word.FOOT, word.RELS):
                    self.assertEqual(original[name], main[name], name)
            original_root, original_p = word.paragraphs(original)
            new_root, new_p = word.paragraphs(main)
            self.assertEqual([word.text_of(x[3]) for x in original_p], [word.text_of(x[3]) for x in new_p])
            self.assertEqual(original_root.xpath("//w:footnote/@w:id", namespaces=word.NS), new_root.xpath("//w:footnote/@w:id", namespaces=word.NS))
            self.assertEqual(len(new_root.findall(".//w:hyperlink", word.NS)), 6)
            rels = word.xml(main[word.RELS])
            for rel in rels:
                target = rel.get("Target")
                self.assertFalse(target.startswith(("/", "file:", "http:")))
                self.assertNotIn(":", target)
                self.assertIn("Submission/"+unquote(target), z.namelist())
            self.assertTrue(any("%20" in rel.get("Target") for rel in rels))
        after = {f.name: f.read_bytes() for f in self.store.folder(p["id"]).glob("inputs/*")}
        self.assertEqual(before, after)

    def test_order_translation_then_original_and_stamp_once(self):
        p = self.ready()
        d = p["documents"][0]
        out = self.store.prepare(p, d)
        reader = PdfReader(BytesIO(out))
        self.assertEqual(len(reader.pages), 3)
        self.assertIn("Northern Beacon", reader.pages[0].extract_text())
        self.assertIn("Северный маяк", reader.pages[1].extract_text())
        for page in reader.pages:
            self.assertEqual(page.extract_text().count("Exhibit RLA-31"), 1)
            self.assertAlmostEqual(float(page.mediabox.height), 841.8898, places=2)
        self.assertEqual(out, self.store.prepare(p, d))

    def test_fragment_has_no_excluded_text_or_original_content_stream(self):
        d = self.p["documents"][1]
        out = self.store.prepare(self.p, d)
        reader = PdfReader(BytesIO(out))
        self.assertEqual(len(reader.pages), 2)
        text = "\n".join(p.extract_text() for p in reader.pages)
        self.assertNotIn("EXCLUDED_PAGE_SECRET", text)
        self.assertNotIn("EXCLUDED_REGION_SECRET", text)
        self.assertNotIn("INCLUDED_FRAGMENT_42", text)  # the retained fragment is pixels, not text
        self.assertIn("страница источника 3", text)
        self.assertEqual(text.count("[...]"), 2)
        for page in reader.pages:
            stream = page.get_contents().get_data()
            self.assertNotIn(b"EXCLUDED_PAGE_SECRET", stream)
            self.assertNotIn(b"EXCLUDED_REGION_SECRET", stream)
        images = list(reader.pages[1].images)
        self.assertEqual(len(images), 1)
        self.assertLess(images[0].image.height, 550)  # only selected pixels, not a full hidden page
        self.assertNotIn("/Annots", reader.pages[1])

    def test_repeat_docx_processing_does_not_duplicate_links_or_formatting(self):
        p = self.ready()
        paths = {d["id"]: safe_path(d["folder"], d["filename"]) for d in p["documents"]}
        one = word.add_links(self.files["Main document.docx"], p["references"], paths)
        rescanned = word.scan(one, [{**d,"identifier":identifier(d)} for d in p["documents"]])["references"]
        two = word.add_links(one, rescanned, paths)
        self.assertEqual(word.package(one), word.package(two))
        # Compare each text character's direct run formatting before/after splitting.
        def styles(data):
            _, rows = word.paragraphs(word.package(data))
            values=[]
            for *_, para in rows:
                for run in para.findall(".//w:r", word.NS):
                    props=run.find("w:rPr", word.NS)
                    props=tuple((c.tag,tuple(c.attrib.items())) for c in props if c.tag not in (f'{{{word.W}}}color',f'{{{word.W}}}u')) if props is not None else ()
                    values.extend((c, props) for c in word.text_of(run))
            return values
        self.assertEqual(styles(self.files["Main document.docx"]), styles(two))

    def test_missing_mention_blocks_export(self):
        self.ready(False)
        unmatched=[i for i in self.store.validate(self.p) if i["code"]=="unmatched"]
        self.assertEqual(len(unmatched),1)
        self.assertIn("RLA-99",unmatched[0]["message"])
        with self.assertRaises(ValueError): self.store.export(self.p)

    def test_repeated_and_multiple_mentions(self):
        refs=self.p["references"]
        self.assertEqual(sum(r["mention"]=="RLA-31" for r in refs),3)
        self.assertEqual(sum(r["footnote"]==3 for r in refs),2)
        self.assertIsNotNone(next(r for r in refs if r["mention"]=="Statement of Claim")["target"])
        r=next(r for r in refs if r["mention"]=="RLA-31")
        self.store.map_reference(self.p,r["key"],self.p["documents"][0]["id"],True)
        self.assertTrue(all(r["manual"] for r in refs if r["mention"]=="RLA-31"))

    def test_duplicate_full_identifiers_and_case_insensitive_paths(self):
        self.ready()
        d=self.p["documents"][1]
        self.store.update(self.p,d["id"],{"number":31})
        self.assertIn("duplicate_id", [i["code"] for i in self.store.validate(self.p)])
        self.store.update(self.p,d["id"],{"prefix":"R", "number":31})
        self.assertNotIn("duplicate_id", [i["code"] for i in self.store.validate(self.p)])
        self.store.update(self.p,d["id"],{"folder":"rla","filename":"RLA-31.PDF"})
        self.assertIn("duplicate_path", [i["code"] for i in self.store.validate(self.p)])

    def test_path_traversal_reserved_names_and_main_directory_collision(self):
        for folder,name in [("../R","a.pdf"),("C:/x","a.pdf"),("a\\b","a.pdf"),("a","../b.pdf"),("a","CON.pdf"),("","x. ")]:
            with self.assertRaises(ValueError): safe_path(folder,name)
        d=self.p["documents"][0]
        self.store.update(self.p,d["id"],{"folder":"Main document.docx"})
        self.assertIn("path_hierarchy",[i["code"] for i in self.store.validate(self.p)])

    def test_changes_invalidate_only_prepared_state_and_links_when_needed(self):
        self.ready()
        d=self.p["documents"][0]
        self.store.update(self.p,d["id"],{"selection":[{"page":2}]})
        self.assertNotEqual(d["approved"],review_digest(d,self.p["style"]))
        self.assertTrue(self.p["links_reviewed"])
        self.store.approve(self.p,d["id"],"document")
        self.store.update(self.p,d["id"],{"filename":"Renamed.pdf"})
        self.assertFalse(self.p["links_reviewed"])
        self.assertFalse(self.p["scanned"])

    def test_replace_source_invalidates_translation_and_persists(self):
        self.ready()
        d=self.p["documents"][0]
        self.store.upload(self.p,"replacement.pdf",self.files["RLA-32.pdf"],"original",d["id"])
        reloaded=Store(self.temp.name).load(self.p["id"])
        changed=reloaded["documents"][0]
        self.assertFalse(changed["translation_confirmed"])
        self.assertIsNone(changed["approved"])
        self.assertEqual(changed["selection"],[{"page":1},{"page":2},{"page":3}])
        self.assertIsNotNone(changed["translation"])

    def test_settings_fragments_and_mappings_survive_new_store(self):
        self.ready()
        loaded=Store(self.temp.name).load(self.p["id"])
        self.assertEqual(self.p,loaded)
        self.assertEqual(self.store.validate(loaded),[])

    def test_source_tampering_is_detected(self):
        self.ready()
        d=self.p["documents"][0]
        path=self.store.folder(self.p["id"])/"inputs"/d["original"]["blob"]
        path.write_bytes(path.read_bytes()+b"tampered")
        self.assertIn("source",[i["code"] for i in self.store.validate(self.p)])
        with self.assertRaises(ValueError):self.store.export(self.p)

    def test_independent_workflow_without_word_keeps_unreferenced_material(self):
        self.p["main"]=None
        self.ready(False)
        self.assertEqual(self.store.validate(self.p),[])
        with ZipFile(BytesIO(self.store.export(self.p))) as z:
            self.assertEqual(len(z.namelist()),4)
            self.assertFalse(any(n.endswith(".docx") for n in z.namelist()))

    def test_links_only_workflow_preserves_all_prepared_pdfs(self):
        self.p= self.store.create("Только ссылки")
        for name in ["RLA-31-original.pdf","RLA-32.pdf","Statement of Claim.pdf","RLA-99.pdf"]:
            self.store.upload(self.p,name,self.files[name])
            d=self.p["documents"][-1]
            self.store.update(self.p,d["id"],{"mode":"passthrough","title":"RLA-31" if name.startswith("RLA-31") else Path(name).stem})
            self.store.approve(self.p,d["id"],"document")
        self.store.upload(self.p,"main.docx",self.files["Main document.docx"],"main")
        self.store.scan(self.p);self.store.confirm_links(self.p)
        with ZipFile(BytesIO(self.store.export(self.p))) as z:
            for name in ["RLA-31-original.pdf","RLA-32.pdf","Statement of Claim.pdf","RLA-99.pdf"]:
                self.assertEqual(z.read("Submission/"+name),self.files[name])

    def test_ambiguous_title_and_custom_mentions_require_choice(self):
        d=self.p["documents"][1]
        self.store.update(self.p,d["id"],{"aliases":["RLA-31"]})
        self.store.scan(self.p)
        self.assertTrue(all(r["target"] is None for r in self.p["references"] if r["mention"]=="RLA-31"))
        f=self.p["footnotes"][0]
        self.store.add_reference(self.p,f["fid"],f["paragraph"],"пункт 1")
        self.assertIsNone(self.p["references"][-1]["target"])
        self.store.scan(self.p)
        self.assertTrue(any(r.get("custom") for r in self.p["references"]))

    def test_invalid_fragments_and_overflowing_stamps(self):
        for selection in [[],[{"page":0}],[{"page":99}],[{"page":1,"rect":[-.1,0,1,1]}],[{"page":1,"rect":[0,0,1,float('nan')]}]]:
            with self.assertRaises(ValueError):pdf.validate_selection(selection,3)
        d=self.p["documents"][0]
        self.store.update(self.p,d["id"],{"original_label":"x"*200})
        with self.assertRaisesRegex(ValueError,"Штампы не помещаются"):self.store.prepare(self.p,d)

    def test_encrypted_invalid_and_scan_detection(self):
        with self.assertRaises(ValueError):pdf.inspect_pdf(b"not pdf")
        writer=PdfWriter();writer.add_blank_page(595,842);writer.encrypt("test")
        output=BytesIO();writer.write(output)
        with self.assertRaisesRegex(ValueError,"Защищённые"):pdf.inspect_pdf(output.getvalue())
        blank=PdfWriter();blank.add_blank_page(595,842);output=BytesIO();blank.write(output)
        self.assertEqual(pdf.inspect_pdf(output.getvalue())["text_pages"],[False])

    def test_complex_word_field_blocks_edit_instead_of_flattening(self):
        self.ready()
        parts=word.package(self.files["Main document.docx"])
        root=word.xml(parts[word.FOOT]);p=root.findall(".//w:footnote",word.NS)[2].find("w:p",word.NS)
        E.SubElement(E.SubElement(p,f"{{{word.W}}}r"),f"{{{word.W}}}fldChar",{f"{{{word.W}}}fldCharType":"begin"})
        parts[word.FOOT]=E.tostring(root)
        data=BytesIO()
        with ZipFile(data,"w",ZIP_DEFLATED) as z:
            for n,v in parts.items():z.writestr(n,v)
        paths={d["id"]:safe_path(d["folder"],d["filename"]) for d in self.p["documents"]}
        with self.assertRaisesRegex(ValueError,"поле"):word.add_links(data.getvalue(),self.p["references"],paths)

    def test_http_security_validation_and_real_export(self):
        import exhibit.app as module
        previous=module.store;module.store=self.store
        try:
            with TestClient(module.app, base_url="http://127.0.0.1") as client:
                self.assertEqual(client.post("/api/projects",json={"name":"bad"}).status_code,403)
                headers={"X-Exhibit-Local":"1"}
                self.assertEqual(client.post("/api/projects",json={"name":"bad"},headers={**headers,"Origin":"https://outside.test"}).status_code,403)
                self.assertEqual(client.get("/api/projects",headers={"Host":"outside.test"}).status_code,400)
                self.assertEqual(client.get("/api/projects").status_code,200)
                self.assertEqual(client.post(f"/api/projects/{self.p['id']}/export",json={},headers=headers).status_code,400)
                self.ready()
                response=client.post(f"/api/projects/{self.p['id']}/export",json={},headers=headers)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers["content-type"],"application/zip")
                self.assertTrue(response.content.startswith(b"PK"))
                preview=client.get(f"/api/projects/{self.p['id']}/documents/{self.p['documents'][0]['id']}/preview?part=result")
                self.assertEqual(preview.status_code,200)
                self.assertTrue(preview.content.startswith(b"\x89PNG"))
        finally:module.store=previous

    def test_rotated_page_and_fragment_dimensions(self):
        writer=PdfWriter()
        writer.add_page(PdfReader(BytesIO(self.files["RLA-32.pdf"])).pages[0]).rotate(90)
        output=BytesIO();writer.write(output)
        meta=pdf.inspect_pdf(output.getvalue())
        self.assertGreater(meta["sizes"][0][0],meta["sizes"][0][1])
        prepared=pdf.prepare_part(output.getvalue(),[{"page":1,"rect":[.1,.1,.8,.5]}],"[Original]","BL-1",{"font":"DejaVu","size":10,"margin":24})
        page=PdfReader(BytesIO(prepared)).pages[0]
        self.assertEqual(len(list(page.images)),1)
        self.assertNotIn("Просим",page.extract_text())
        self.assertTrue(pdf.render_png(prepared,1).startswith(b"\x89PNG"))

    def test_unnumbered_exhibit_cannot_be_approved(self):
        p=self.store.create("Новый проект")
        self.store.upload(p,"new.pdf",self.files["RLA-32.pdf"])
        self.assertEqual(p["documents"][0]["prefix"],"")
        with self.assertRaisesRegex(ValueError,"номер приложения"):
            self.store.approve(p,p["documents"][0]["id"],"document")

    def test_replacing_part_of_existing_link_preserves_neighbor_links(self):
        self.ready()
        parts = word.package(self.files["Main document.docx"])
        root = word.xml(parts[word.FOOT])
        para = root.findall("w:footnote", word.NS)[2].find("w:p", word.NS)
        original_text = word.text_of(para)
        link = E.Element(f"{{{word.W}}}hyperlink", {f"{{{word.R}}}id": "rIdOld"})
        for child in list(para):
            if word.text_of(child): link.append(child)
        para.append(link)
        rels = E.Element(f"{{{word.REL}}}Relationships")
        E.SubElement(rels, f"{{{word.REL}}}Relationship", Id="rIdOld", Type=word.R+"/hyperlink", Target="https://example.invalid/source", TargetMode="External")
        parts[word.FOOT], parts[word.RELS] = E.tostring(root), E.tostring(rels)
        data = BytesIO()
        with ZipFile(data, "w", ZIP_DEFLATED) as z:
            for name, content in parts.items(): z.writestr(name, content)
        paths = {d["id"]: safe_path(d["folder"],d["filename"]) for d in self.p["documents"]}
        result = word.add_links(data.getvalue(), self.p["references"], paths)
        result_para = word.paragraphs(word.package(result))[1][0][3]
        self.assertEqual(word.text_of(result_para), original_text)
        neighbors = result_para.xpath('./w:hyperlink[@r:id="rIdOld"]', namespaces=word.NS)
        self.assertEqual("".join(word.text_of(n) for n in neighbors), original_text.replace("RLA-31", ""))
        self.assertEqual(word.package(result), word.package(word.add_links(result, self.p["references"], paths)))

    def test_new_upload_requires_rescan_and_unicode_paths_conflict(self):
        self.ready()
        self.store.upload(self.p, "additional.pdf", self.files["RLA-99.pdf"])
        self.assertFalse(self.p["scanned"])
        with self.assertRaisesRegex(ValueError, "заново"): self.store.confirm_links(self.p)
        for d, name in zip(self.p["documents"], ["Caf\u00e9.pdf", "Cafe\u0301.pdf"]):
            self.store.update(self.p, d["id"], {"filename": name, "folder": ""})
        self.assertIn("duplicate_path", [i["code"] for i in self.store.validate(self.p)])

    def test_identifier_is_not_matched_inside_a_longer_identifier(self):
        parts = word.package(self.files["Main document.docx"])
        root = word.xml(parts[word.FOOT])
        for node in root.findall(".//w:t", word.NS):
            node.text = (node.text or "").replace("31", "31-EXTRA").replace("RLA-99", "rla-99")
        # The fixture intentionally splits RLA-99 across runs as well.
        nodes = root.findall(".//w:t", word.NS)
        nodes[-2].text = nodes[-2].text.replace("RLA-", "rla-")
        parts[word.FOOT] = E.tostring(root)
        data = BytesIO()
        with ZipFile(data, "w", ZIP_DEFLATED) as z:
            for name, content in parts.items(): z.writestr(name, content)
        documents = [{**d, "identifier": identifier(d)} for d in self.p["documents"]]
        refs = word.scan(data.getvalue(), documents)["references"]
        self.assertFalse(any(r["mention"] == "RLA-31" for r in refs))
        self.assertTrue(any(r["mention"] == "rla-99" and r["target"] is None for r in refs))


if __name__ == "__main__":
    unittest.main()
