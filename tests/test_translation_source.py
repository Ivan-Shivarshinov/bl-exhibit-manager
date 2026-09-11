from io import BytesIO
import tempfile
import unittest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen import canvas
from exhibit import pdf
from exhibit.project import Store, LOCK
from exhibit.translation import Translations
from exhibit.translation_source import extract

def fixture():
    pdf.init_font(); buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(600,800))
    for number in range(1,3):
        c.setFont('DejaVu',10)
        c.drawString(60,770,f'HEADER-{number}')
        c.drawString(60,600,f'BODY-{number}: 15 March 2026, EUR 1250, RLA-31')
        c.drawString(60,400,f'OTHER-{number}: DO NOT SEND')
        c.drawString(60,90,f'FOOTNOTE-{number}: meaning-bearing note')
        c.drawString(60,35,f'FOOTER-{number}');c.drawRightString(550,35,str(number));c.showPage()
    c.save();return buf.getvalue()

class SourceSelectionTests(unittest.TestCase):
    def setUp(self): self.data=fixture()
    def text(self,options,data=None): return '\n'.join(x['source'] for x in extract(data or self.data,options)[1])

    def test_default_bands_exclude_footer_header_but_keep_footnote(self):
        text=self.text({'selection':[{'page':1},{'page':2}],'top':60,'bottom':60})
        self.assertIn('BODY-1',text);self.assertIn('FOOTNOTE-1',text)
        self.assertNotIn('HEADER',text);self.assertNotIn('FOOTER',text);self.assertNotIn('\n1',text)

    def test_only_selected_page_and_region_is_sent(self):
        text=self.text({'selection':[{'page':2,'rect':[0,.2,1,.35]}],'top':60,'bottom':60})
        self.assertIn('BODY-2',text)
        for excluded in ('BODY-1','OTHER','FOOTNOTE','FOOTER','HEADER'):self.assertNotIn(excluded,text)

    def test_multiple_regions_preserve_user_order(self):
        opts={'selection':[{'page':2,'rect':[0,.48,1,.55]},{'page':1,'rect':[0,.2,1,.35]}],'top':0,'bottom':0}
        parts=extract(self.data,opts)[1]
        self.assertEqual([p['page'] for p in parts],[2,1])
        self.assertIn('OTHER-2',parts[0]['source']);self.assertIn('BODY-1',parts[1]['source'])

    def test_zero_bands_restore_header_footer_explicitly(self):
        text=self.text({'selection':[{'page':1}],'top':0,'bottom':0})
        self.assertIn('HEADER-1',text);self.assertIn('FOOTER-1',text)

    def test_rotation_coordinates_match_display(self):
        writer=PdfWriter();page=writer.add_page(PdfReader(BytesIO(self.data)).pages[0]);page.rotate(90)
        buf=BytesIO();writer.write(buf)
        text=self.text({'selection':[{'page':1,'rect':[.72,.07,.80,.75]}],'top':0,'bottom':0},buf.getvalue())
        self.assertIn('BODY-1',text);self.assertNotIn('OTHER',text);self.assertNotIn('HEADER',text)

    def test_cropbox_offset_matches_display(self):
        writer=PdfWriter();page=writer.add_page(PdfReader(BytesIO(self.data)).pages[0]);page.cropbox=RectangleObject([40,300,560,700])
        buf=BytesIO();writer.write(buf)
        text=self.text({'selection':[{'page':1,'rect':[0,.2,1,.3]}],'top':0,'bottom':0},buf.getvalue())
        self.assertIn('BODY-1',text);self.assertNotIn('OTHER',text)

    def test_invalid_empty_and_hidden_selections_fail(self):
        for opts in ({'selection':[],'top':0,'bottom':0},{'selection':[{'page':3}],'top':0,'bottom':0},
                     {'selection':[{'page':1}],'top':float('nan'),'bottom':0},
                     {'selection':[{'page':1,'rect':[0,.95,1,1]}],'top':60,'bottom':60}):
            with self.assertRaises(ValueError):self.text(opts)

    def test_boundary_through_letters_is_rejected_instead_of_damaged_text(self):
        with self.assertRaisesRegex(ValueError,'пересекает буквы'):
            self.text({'selection':[{'page':1,'rect':[0,.249,1,.26]}],'top':0,'bottom':0})

    def test_saved_choice_and_token_protect_actual_model_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder);p=store.create('Selection');store.upload(p,'source.pdf',self.data);did=p['documents'][0]['id'];received=[]
            def fake(provider,text,target,cancel): received.append(text);return 'Verified test double'
            manager=Translations(store,fake)
            opts={'selection':[{'page':2,'rect':[0,.2,1,.35]}],'top':60,'bottom':60}
            selected=manager.preview_source(p['id'],did,opts)
            self.assertEqual(Translations(store,fake).source(p['id'],did)['options'],opts)
            body={'provider':'codex','target':'English','reviewed_source':True,'options':opts,'token':selected['token']}
            with self.assertRaises(ValueError):manager.start(p['id'],did,{**body,'options':{'selection':[{'page':1}],'top':0,'bottom':0}})
            with LOCK:
                manager.start(p['id'],did,body);thread=manager.active[p['id'],did][1]
            thread.join(5)
            self.assertFalse(thread.is_alive());self.assertEqual(received,[selected['parts'][0]['source']])
            self.assertNotIn('OTHER',received[0]);self.assertNotIn('FOOTER',received[0]);self.assertNotIn('BODY-1',received[0])

if __name__=='__main__':unittest.main()
