from io import BytesIO
from copy import deepcopy
import tempfile
import unittest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen import canvas
from exhibit import pdf
from exhibit.translation import draft_pdf
from exhibit.translation import Translations
from exhibit.project import Store, LOCK
from exhibit.translation_source import extract


def fixture():
    pdf.init_font(); stream=BytesIO(); c=canvas.Canvas(stream,pagesize=(600,800))
    for page in (1,2):
        c.setFont('DejaVu',10);c.drawString(48,770,f'HEADER {page}')
        c.drawString(48,660,f'Contract {page}: EUR 1250. See note [1].')
        c.drawString(48,120,f'[1] FOOTNOTE {page}: this exception is material.')
        c.drawString(48,35,f'FOOTER {page}');c.drawRightString(552,35,str(page));c.showPage()
    c.save();return stream.getvalue()


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.data=fixture()
        self.options={'selection':[{'page':1},{'page':2}], 'top':60,'bottom':60,'placement':'preserve','notes':[{'page':1,'height':100},{'page':2,'height':100}]}

    def state(self,options=None):
        options,parts=extract(self.data,options or self.options)
        for p in parts:p['translation']=p['source'] # Explicit identity double, not a model translation.
        return {'parts':parts,'source_options':options,'target':'English'}

    def boxes(self,data):
        result=[]
        with pdf.PDF_LOCK,pdf.pdfium.PdfDocument(data) as doc:
            for page in doc:
                text=page.get_textpage()
                try:
                    chars=[]
                    for i in range(text.count_chars()):
                        code=pdf.pdfium.raw.FPDFText_GetUnicode(text,i)
                        if code:chars.append((chr(code),text.get_charbox(i)))
                    result.append(chars)
                finally:text.close();page.close()
        return result

    def test_extract_roles_once_with_numbers_and_page_isolation(self):
        state=self.state()
        for page in (1,2):
            parts=[x for x in state['parts'] if x['page']==page]
            self.assertEqual([x['role'] for x in parts],['header','body','footnotes','footer','footer'])
            self.assertEqual(parts[-1]['source'],str(page))
            self.assertNotIn('FOOTNOTE',parts[1]['source']);self.assertIn('[1]',parts[1]['source'])
            self.assertIn(f'FOOTNOTE {page}',parts[2]['source'])

    def test_new_default_translates_furniture_and_old_options_still_exclude(self):
        opts,parts=extract(self.data,None)
        self.assertEqual(opts['placement'],'preserve');self.assertIn('footer',[x['role'] for x in parts])
        _,old=extract(self.data,{'selection':[{'page':1}],'top':60,'bottom':60})
        self.assertNotIn('FOOTER',''.join(x['source'] for x in old))

    def test_only_selected_body_region_no_furniture_or_notes_leak(self):
        options={**self.options,'selection':[{'page':2,'rect':[0,.1,1,.25]}]}
        parts=self.state(options)['parts'];self.assertEqual([x['role'] for x in parts],['body'])
        self.assertNotIn('FOOTNOTE',parts[0]['source']);self.assertNotIn('HEADER',parts[0]['source'])

    def test_page_specific_notes_and_no_duplicate_body_text(self):
        state=self.state({**self.options,'notes':[{'page':2,'height':100}]})
        self.assertNotIn('footnotes',[p['role'] for p in state['parts'] if p['page']==1])
        self.assertEqual(sum('FOOTNOTE 2' in p['source'] for p in state['parts']),1)

    def test_invalid_notes_and_boundary_fail(self):
        for notes in ([{'page':3,'height':100}],[{'page':1,'height':float('nan')}],[{'page':1,'height':100}]*2):
            with self.assertRaises(ValueError):self.state({**self.options,'notes':notes})
        with self.assertRaisesRegex(ValueError,'пересекает'):
            self.state({**self.options,'notes':[{'page':1,'height':65}]})

    def test_pdf_notes_stay_below_body_and_footer_page_number_on_right(self):
        data=draft_pdf(self.state());reader=PdfReader(BytesIO(data));self.assertEqual(len(reader.pages),2)
        for index,page in enumerate(reader.pages,1):
            text=page.extract_text();self.assertEqual(text.count(f'FOOTNOTE {index}'),1)
            self.assertIn(f'HEADER {index}',text);self.assertNotIn(f'FOOTNOTE {3-index}',text)
        chars=self.boxes(data)[0]
        # Characters are emitted furniture first, then body, then footnotes.
        body_start=next(i for i,x in enumerate(chars) if ''.join(y[0] for y in chars[i:i+8])=='Contract')
        note_start=next(i for i,x in enumerate(chars) if ''.join(y[0] for y in chars[i:i+8])=='FOOTNOTE')
        self.assertGreater(chars[body_start][1][1],chars[note_start][1][3]+20)
        self.assertLess(chars[note_start][1][3],180)
        self.assertTrue(any(c=='1' and box[0]>500 and box[3]<60 for c,box in chars))

    def test_long_notes_expand_upward_without_overlapping_body(self):
        state=self.state()
        for p in state['parts']:
            if p['role']=='footnotes':p['translation']='[1] '+('Material exception and qualification. '*65)
        result=draft_pdf(state);self.assertEqual(len(PdfReader(BytesIO(result)).pages),2)
        self.assertEqual(PdfReader(BytesIO(result)).pages[0].extract_text().count('Material exception'),65)
        chars=self.boxes(result)[0]
        start=next(i for i,x in enumerate(chars) if ''.join(y[0] for y in chars[i:i+8])=='Contract')
        note=next(i for i,x in enumerate(chars) if ''.join(y[0] for y in chars[i:i+8])=='Material')
        self.assertGreater(chars[start][1][1],chars[note][1][3]+20)

    def test_job_save_restart_apply_and_changed_notes_require_new_consent(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder);project=store.create('Structured translation');store.upload(project,'Original.pdf',self.data)
            pid,did=project['id'],project['documents'][0]['id'];seen=[]
            def identity(provider,text,target,cancel):seen.append(text);return text
            manager=Translations(store,identity);source=manager.preview_source(pid,did,self.options)
            body={'provider':'codex','target':'English','reviewed_source':True,'options':self.options,'token':source['token']}
            with self.assertRaises(ValueError):manager.start(pid,did,{**body,'options':{**self.options,'notes':[]}})
            with LOCK:
                manager.start(pid,did,body);thread=manager.active[pid,did][1]
            thread.join(10);self.assertFalse(thread.is_alive())
            manager=Translations(store,identity);state=manager.state(pid,did)
            self.assertEqual(state['status'],'complete');self.assertEqual(seen,[p['source'] for p in state['parts']])
            texts=[p['translation'] for p in state['parts']];index=next(i for i,p in enumerate(state['parts']) if p['role']=='footnotes');texts[index]='[1] Saved edited footnote.'
            state=manager.save(pid,did,{'revision':state['revision'],'texts':texts})
            document=manager.preview(pid,did,state['revision']);self.assertIn('Saved edited footnote',PdfReader(BytesIO(document)).pages[0].extract_text())
            manager.apply(pid,did,{'revision':state['revision'],'confirmed':True})
            stored=store.load(pid);self.assertEqual(store.source(stored,store.document(stored,did)['original']),self.data)
            self.assertEqual(store.source(stored,store.document(stored,did)['translation']),document)

    def test_excessive_notes_reject_instead_of_truncate_or_move_page(self):
        state=self.state();next(p for p in state['parts'] if p['role']=='footnotes')['translation']='Long legal note. '*3000
        with self.assertRaisesRegex(ValueError,'не помещаются'):draft_pdf(state)

    def test_furniture_overflow_rejects_and_markup_is_literal(self):
        state=self.state();state['parts'][0]['translation']='Long header. '*1000
        with self.assertRaisesRegex(ValueError,'колонтитула'):draft_pdf(state)
        state=self.state();next(p for p in state['parts'] if p['role']=='footnotes')['translation']='[1] <literal> & exception'
        self.assertIn('<literal> & exception',PdfReader(BytesIO(draft_pdf(state))).pages[0].extract_text())

    def test_rotation_and_crop_have_normalized_layout(self):
        writer=PdfWriter();page=writer.add_page(PdfReader(BytesIO(self.data)).pages[0]);page.rotate(90)
        buf=BytesIO();writer.write(buf)
        opts,parts=extract(buf.getvalue(),{'selection':[{'page':1}],'top':0,'bottom':0,'placement':'preserve','notes':[]})
        self.assertEqual(parts[0]['layout']['size'],[800,600])
        writer=PdfWriter();page=writer.add_page(PdfReader(BytesIO(self.data)).pages[0]);page.cropbox=RectangleObject([40,70,560,730]);buf=BytesIO();writer.write(buf)
        opts,parts=extract(buf.getvalue(),{'selection':[{'page':1}],'top':0,'bottom':0,'placement':'preserve','notes':[]})
        self.assertEqual(parts[0]['layout']['size'],[520,660]);self.assertNotIn('FOOTER',''.join(x['source'] for x in parts))

if __name__=='__main__':unittest.main()
