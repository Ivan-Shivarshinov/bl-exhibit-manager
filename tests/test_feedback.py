from unittest import TestCase
from tempfile import TemporaryDirectory
from io import BytesIO
from pypdf import PdfReader
from exhibit.project import Store,identifier
from exhibit.samples import make_pdf,main_docx
from exhibit import word
from exhibit import pdf
from exhibit.project import DEFAULT_STYLE,revision,review_digest,effective_document
from reportlab.pdfgen import canvas
from PIL import Image
from zipfile import ZipFile,ZIP_DEFLATED
from lxml import etree as E
from exhibit import citations


def citation_docx(texts):
    parts=word.package(main_docx());root=word.xml(parts[word.FOOT])
    body=word.xml(parts['word/document.xml'])
    for ref in body.findall('.//w:footnoteReference',word.NS):
        if int(ref.get(f'{{{word.W}}}id'))>len(texts):ref.getparent().remove(ref)
    parts['word/document.xml']=E.tostring(body)
    for i,text in enumerate(texts,1):
        note=root.find(f"w:footnote[@w:id='{i}']",word.NS);note[:]=[]
        p=E.SubElement(note,'{'+word.W+'}p');r=E.SubElement(p,'{'+word.W+'}r');E.SubElement(r,'{'+word.W+'}t').text=text
    for note in list(root):
        if int(note.get('{'+word.W+'}id'))>len(texts):root.remove(note)
    parts[word.FOOT]=E.tostring(root);out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as z:
        for name,data in parts.items():z.writestr(name,data)
    return out.getvalue()


class CitationFeedback(TestCase):
    def test_neutral_style_overrides_theme_and_keeps_font_bold_italic_and_size(self):
        run=E.Element(f'{{{word.W}}}r');props=E.SubElement(run,f'{{{word.W}}}rPr')
        for name,attrs in [('rFonts',{'ascii':'Times New Roman'}),('b',{}),('i',{}),('color',{'val':'996699','themeColor':'hyperlink'}),('sz',{'val':'20'}),('u',{'val':'single','themeColor':'hyperlink'})]:
            E.SubElement(props,f'{{{word.W}}}{name}',{f'{{{word.W}}}{key}':value for key,value in attrs.items()})
        preserved=[E.tostring(x) for x in props if E.QName(x).localname not in ('color','u')]
        word.neutral_link_style(run)
        self.assertEqual([E.tostring(x) for x in props if E.QName(x).localname not in ('color','u')],preserved)
        self.assertEqual(props.find('w:color',word.NS).attrib,{f'{{{word.W}}}val':'000000'})
        self.assertEqual(props.find('w:u',word.NS).attrib,{f'{{{word.W}}}val':'none'})
        first=E.tostring(run);word.neutral_link_style(run);self.assertEqual(first,E.tostring(run))
    def test_identifier_and_known_title_are_one_link_and_nbsp_matches(self):
        docs=[{'id':'a','identifier':'Annex 1','title':'Training Response','aliases':[]}]
        data=citation_docx(['Annex\u00a01, Training Response, para. 1; Annex 1, Training Response, para. 2.'])
        refs=word.scan(data,docs)['references']
        self.assertEqual([r['link_text'] for r in refs],['Annex\u00a01, Training Response','Annex 1, Training Response'])
        self.assertTrue(all(r['target']=='a' for r in refs))
    def test_full_titles_multiple_documents_dates_and_internal_commas(self):
        texts=['RLA-12, Training Statute, Articles 1(1), 2(1);',
            'First Expert Report of Alex Sample, paras. 32 and 38.',
            'Amendment to the Training Response, paras. 5-6; R-19, Response to the Training Request, dated 10 April 2026, paras. 9-10.',
            'R-1, Training Statement, Annex No. 6 to Building Contract 100001, Clause 5; R-2, Training Statement, Annex No. 6 to Building Contract 100002, Clause 5.',
            'Annex 1, Second Training Response.']
        labels=['RLA-12','First Expert Report of Alex Sample','Amendment to the Training Response','R-19','R-1','R-2','Annex 1']
        docs=[{'id':str(i),'identifier':name if name.startswith(('R','Annex')) else '', 'title':name,'aliases':[]} for i,name in enumerate(labels)]
        found=word.scan(citation_docx(texts),docs)
        self.assertEqual([r['link_text'] for r in found['references']],['RLA-12, Training Statute',labels[1],labels[2],'R-19, Response to the Training Request, dated 10 April 2026','R-1, Training Statement, Annex No. 6 to Building Contract 100001','R-2, Training Statement, Annex No. 6 to Building Contract 100002','Annex 1, Second Training Response'])
        linked=word.add_links(citation_docx(texts),found['references'],{d['id']:d['id']+'.pdf' for d in docs})
        root=word.xml(word.package(linked)[word.FOOT])
        self.assertEqual([word.text_of(h) for h in root.findall('.//w:hyperlink',word.NS)],[r['link_text'] for r in found['references']])
    def test_manual_extent_persists_across_scan_and_rejects_overlap(self):
        with TemporaryDirectory() as folder:
            s=Store(folder);p=s.create('Ranges')
            for i in (1,2):
                s.upload(p,f'Training {i}.pdf',make_pdf('Training',[['Fictional']]))
                s.update(p,p['documents'][-1]['id'],{'prefix':'R','number':i})
            s.upload(p,'Main.docx',citation_docx(['R-1, First Training Response, p. 5; R-2, Second Training Response, p. 6.']),'main');s.scan(p)
            ref=p['references'][0]
            s.reference_range(p,ref['key'],ref['start'],ref['end']);s.scan(p)
            self.assertEqual(p['references'][0]['link_text'],'R-1')
            with self.assertRaisesRegex(ValueError,'пересекаются'):
                s.reference_range(p,ref['key'],ref['start'],len(p['footnotes'][0]['text']))
            self.assertEqual(s.load(p['id'])['references'][0]['link_text'],'R-1')
    def test_legacy_ranges_stay_short_until_rescan(self):
        text='RLA-12, Training Statute, Article 1.';data=citation_docx([text]);docs=[{'id':'a','identifier':'RLA-12','title':'Statute','aliases':[]}]
        refs=word.scan(data,docs)['references']
        for ref in refs:
            for key in ('link_start','link_end','link_text'):ref.pop(key,None)
        linked=word.add_links(data,refs,{'a':'Statute.pdf'})
        self.assertEqual(word.text_of(word.xml(word.package(linked)[word.FOOT]).find('.//w:hyperlink',word.NS)),'RLA-12')


class NumberingFeedback(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.p=self.store.create('Feedback')
    def add(self,designation='Annex',number=None):
        self.store.upload(self.p,f'Original{len(self.p["documents"])}.pdf',make_pdf('Training',[['Fictional material']]))
        d=self.p['documents'][-1]
        self.store.update(self.p,d['id'],{'designation':designation,'number':number,'prefix':''})
        return d
    def test_unprefixed_annex_stamp_and_distinct_exhibit(self):
        a=self.add(number=1);b=self.add('Exhibit',1)
        self.assertEqual(identifier(a,self.p),'Annex 1');self.assertEqual(identifier(b,self.p),'Exhibit 1')
        text=PdfReader(BytesIO(self.store.prepare(self.p,a))).pages[0].extract_text()
        self.assertIn('Annex 1',text);self.assertNotIn('Annex Annex',text);self.assertNotIn('-1',text)
        self.assertNotIn('duplicate_id',[x['code'] for x in self.store.validate(self.p)])
        self.add(number=1);self.assertIn('duplicate_id',[x['code'] for x in self.store.validate(self.p)])
    def test_batch_empty_prefix_skips_occupied_and_generates_names(self):
        self.add(number=1);a=self.add();b=self.add()
        proposed,report=self.store.batch_plan(self.p,{'action':'organize','document_ids':[a['id'],b['id']], 'numbering':{'prefix':'','start':1},'names':'identifier'})
        self.assertFalse(report['conflicts'])
        self.assertEqual([(d['number'],d['filename']) for d in proposed['documents'][1:]],[(2,'Annex 2.pdf'),(3,'Annex 3.pdf')])
    def test_inherited_designation_changes_identity_and_invalidates_links(self):
        a=self.add(number=1);self.store.update(self.p,a['id'],{'format_overrides':{}})
        self.p['scanned']=self.p['links_reviewed']=True
        proposed,report=self.store.batch_plan(self.p,{'action':'format','scope':'project','values':{'designation':'Annex'}})
        self.assertEqual(identifier(proposed['documents'][0],proposed),'Annex 1')
        self.assertFalse(proposed['scanned']);self.assertTrue(report['links_need_review'])
    def test_numeric_label_is_not_detected_as_arbitrary_number(self):
        a=self.add('',1)
        result=word.scan(main_docx(),[{**a,'identifier':'1','title':'1'}])
        self.assertFalse(any(r['target']==a['id'] for r in result['references']))


class StampFeedback(TestCase):
    def source(self,occupied=False,rotate=False):
        out=BytesIO();c=canvas.Canvas(out,pagesize=(600,800));c.drawString(60,650,'Unchanged text position')
        if occupied:c.setFillColorRGB(0,0,0);c.rect(450,770,125,20,fill=1,stroke=0)
        c.save();data=out.getvalue()
        if rotate:
            from pypdf import PdfWriter
            writer=PdfWriter();p=writer.add_page(PdfReader(BytesIO(data)).pages[0]);p.rotate(90);out=BytesIO();writer.write(out);data=out.getvalue()
        return data
    def test_overlay_preserves_size_and_source_pixels_below_stamp(self):
        data=self.source();style={**DEFAULT_STYLE,'stamp_mode':'overlay','top':26}
        result=pdf.prepare_part(data,[{'page':1}],'','Annex 1',style)
        reader=PdfReader(BytesIO(result));self.assertEqual(tuple(reader.pages[0].mediabox),(0,0,600,800))
        before=Image.open(BytesIO(pdf.render_png(data,1,1))).convert('RGB');after=Image.open(BytesIO(pdf.render_png(result,1,1))).convert('RGB')
        self.assertEqual(before.crop((0,70,600,800)).tobytes(),after.crop((0,70,600,800)).tobytes())
    def test_collision_is_blocked_and_can_be_resolved_by_offset_or_band(self):
        data=self.source(True)
        with self.assertRaisesRegex(ValueError,'пересекает'):pdf.prepare_part(data,[{'page':1}],'','Annex 1',{**DEFAULT_STYLE,'stamp_mode':'overlay'})
        for style in ({**DEFAULT_STYLE,'stamp_mode':'overlay','top':70},DEFAULT_STYLE):
            self.assertIn('Annex 1',PdfReader(BytesIO(pdf.prepare_part(data,[{'page':1}],'','Annex 1',style))).pages[0].extract_text())
    def test_legacy_projects_keep_band_and_approval_digest(self):
        with TemporaryDirectory() as folder:
            s=Store(folder);p=s.create('Old');p['style']=dict(DEFAULT_STYLE)
            s.upload(p,'Old.pdf',self.source());d=p['documents'][0];s.update(p,d['id'],{'prefix':'RLA','number':1})
            effective,style=effective_document(p,d);self.assertEqual(style,DEFAULT_STYLE)
            d['approved']=review_digest(effective,DEFAULT_STYLE)
            self.assertEqual(d['approved'],revision(p,d))
            result=PdfReader(BytesIO(s.prepare(p,d)));self.assertEqual(float(result.pages[0].mediabox.height),854)
    def test_rotation_keeps_visual_page_dimensions(self):
        data=self.source(rotate=True)
        result=pdf.prepare_part(data,[{'page':1}],'','Annex 1',{**DEFAULT_STYLE,'stamp_mode':'overlay'})
        self.assertEqual(tuple(PdfReader(BytesIO(result)).pages[0].mediabox),(0,0,800,600))
