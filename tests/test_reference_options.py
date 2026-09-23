from io import BytesIO
from tempfile import TemporaryDirectory
from unittest import TestCase
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as E
from exhibit import word, main_pdf
from exhibit.project import Store
from exhibit.samples import make_pdf
from tests.test_feedback import citation_docx


def pack(parts):
    out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as z:
        for name,data in parts.items():z.writestr(name,data)
    return out.getvalue()


def video_fixture():
    data=citation_docx(['Annex 1, Training Request, para. 3; Annex 76, Training video at ',
                        'Annex 76, Training video at '])
    parts=word.package(data);root,rows=word.paragraphs(parts)
    rels=E.Element(f'{{{word.REL}}}Relationships',nsmap={None:word.REL})
    for i,(*_,p) in enumerate(rows):
        rid=f'rIdVideo{i}'
        link=E.SubElement(p,f'{{{word.W}}}hyperlink',{f'{{{word.R}}}id':rid})
        r=E.SubElement(link,f'{{{word.W}}}r');props=E.SubElement(r,f'{{{word.W}}}rPr')
        E.SubElement(props,f'{{{word.W}}}color',{f'{{{word.W}}}val':'0000FF'})
        E.SubElement(props,f'{{{word.W}}}u',{f'{{{word.W}}}val':'single'})
        url=f'https://www.youtube.com/watch?v=training&t={10+i}'
        E.SubElement(r,f'{{{word.W}}}t').text=url
        E.SubElement(rels,f'{{{word.REL}}}Relationship',Id=rid,Type=word.R+'/hyperlink',Target=url,TargetMode='External')
    parts[word.FOOT]=E.tostring(root);parts[word.RELS]=E.tostring(rels)
    return pack(parts)


class ReferenceOptions(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=Store(self.temp.name);self.p=self.s.create('Reference choices')
        self.s.upload(self.p,'Annex 1.pdf',make_pdf('Training',[['Fictional document']]))
        self.d=self.p['documents'][0]
        self.s.update(self.p,self.d['id'],{'prefix':'','designation':'Annex','number':1})
        self.s.approve(self.p,self.d['id'],'document')
        self.data=video_fixture();self.s.upload(self.p,'Main.docx',self.data,'main');self.s.scan(self.p)

    def test_explicit_no_file_persists_preserves_urls_and_all_other_parts(self):
        with self.assertRaisesRegex(ValueError,'несопоставленные'):self.s.confirm_links(self.p)
        ref=next(r for r in self.p['references'] if r['mention']=='Annex 76')
        self.s.map_reference(self.p,ref['key'],None,all_same=True,keep_original=True)
        self.p=self.s.load(self.p['id']);self.s.scan(self.p)
        videos=[r for r in self.p['references'] if r['mention']=='Annex 76']
        self.assertEqual(len(videos),2);self.assertTrue(all(r['keep_original'] and r['target'] is None for r in videos))
        self.s.confirm_links(self.p);self.assertEqual(self.s.validate(self.p),[])
        linked,paths=self.s.linked_main(self.p)
        before=word.package(self.data);after=word.package(linked)
        for key in before:
            if key not in (word.FOOT,word.RELS):self.assertEqual(before[key],after[key])
        oldroot,oldrows=word.paragraphs(before);newroot,newrows=word.paragraphs(after)
        self.assertEqual([word.text_of(p) for *_,p in oldrows],[word.text_of(p) for *_,p in newrows])
        for i in range(2):
            old=oldroot.xpath(f'.//w:hyperlink[@r:id="rIdVideo{i}"]',namespaces=word.NS)[0]
            new=newroot.xpath(f'.//w:hyperlink[@r:id="rIdVideo{i}"]',namespaces=word.NS)[0]
            self.assertEqual(E.tostring(old),E.tostring(new))
        self.assertEqual(E.tostring(oldrows[1][-1]),E.tostring(newrows[1][-1]))
        _,markers=main_pdf.marked_docx(linked,list(paths.values()))
        self.assertEqual(list(markers.values()),['Annex 1.pdf'])

    def test_choices_are_reversible_and_unresolved_is_not_ignored(self):
        ref=self.p['references'][1]
        self.s.map_reference(self.p,ref['key'],None,keep_original=True)
        self.s.map_reference(self.p,ref['key'],self.d['id'])
        self.assertFalse(ref['keep_original']);self.assertEqual(ref['target'],self.d['id'])
        self.s.map_reference(self.p,ref['key'],None)
        self.assertFalse(ref['keep_original'])
        self.assertTrue(any(i['reference']==ref['key'] for i in self.s.validate(self.p)))
        with self.assertRaises(ValueError):self.s.map_reference(self.p,ref['key'],self.d['id'],keep_original=True)
        with self.assertRaises(ValueError):self.s.map_reference(self.p,ref['key'],None,keep_original='true')

    def test_all_no_file_returns_exact_original_and_can_have_no_pdfs(self):
        p=self.s.create('External only');self.s.upload(p,'Main.docx',self.data,'main');self.s.scan(p)
        for r in p['references']:self.s.map_reference(p,r['key'],None,keep_original=True)
        self.s.confirm_links(p);self.assertEqual(self.s.validate(p),[])
        self.assertEqual(self.s.linked_main(p)[0],self.data)
        self.s.upload(p,'Replacement.docx',self.data,'main')
        self.s.scan(p);self.assertFalse(any(r['keep_original'] for r in p['references']))


class MixedWordRuns(TestCase):
    def test_mixed_layout_run_and_repeated_annex_are_preserved(self):
        parts=word.package(citation_docx(['placeholder']));root,rows=word.paragraphs(parts);p=rows[0][-1];p[:]=[]
        r=E.SubElement(p,f'{{{word.W}}}r')
        E.SubElement(r,f'{{{word.W}}}footnoteRef')
        E.SubElement(r,f'{{{word.W}}}tab')
        for name,value in [('t','Annex 3, Training Request, dated 2 September 2026, para. 3;'),('br',None),
                           ('t','Annex 66, Regulation 833/2014, Annex XIX.')]:
            node=E.SubElement(r,f'{{{word.W}}}{name}');node.text=value
        parts[word.FOOT]=E.tostring(root);data=pack(parts)
        refs=word.scan(data,[{'id':str(n),'identifier':f'Annex {n}','title':f'Annex {n}','aliases':[]} for n in (3,66)])['references']
        self.assertEqual(len(refs),2)
        result=word.add_links(data,refs,{'3':'Annex 3.pdf','66':'Annex 66.pdf'})
        _,newrows=word.paragraphs(word.package(result));new=newrows[0][-1]
        self.assertEqual(word.text_of(p),word.text_of(new))
        self.assertEqual([word.text_of(h) for h in new.findall('w:hyperlink',word.NS)],[r['link_text'] for r in refs])
        self.assertEqual(len(new.findall('.//w:footnoteRef',word.NS)),1)
        self.assertEqual(len(new.findall('.//w:tab',word.NS)),1)
        self.assertEqual(len(new.findall('.//w:br',word.NS)),1)
        self.assertFalse(new.findall('.//w:hyperlink//w:footnoteRef',word.NS))

    def test_atoms_inside_existing_link_retain_xml_and_outside_text(self):
        parts=word.package(citation_docx(['placeholder']));root,rows=word.paragraphs(parts);p=rows[0][-1];p[:]=[]
        h=E.SubElement(p,f'{{{word.W}}}hyperlink',{f'{{{word.R}}}id':'old'})
        r=E.SubElement(h,f'{{{word.W}}}r')
        for name,value in [('t','before Annex 66, Training'),('br',None),('t','Report'),('tab',None),
                           ('noBreakHyphen',None),('softHyphen',None),('lastRenderedPageBreak',None),('t',', para. 1; after')]:
            node=E.SubElement(r,f'{{{word.W}}}{name}');node.text=value
        parts[word.FOOT]=E.tostring(root)
        rels=E.Element(f'{{{word.REL}}}Relationships',nsmap={None:word.REL})
        E.SubElement(rels,f'{{{word.REL}}}Relationship',Id='old',Type=word.R+'/hyperlink',Target='https://example.org/',TargetMode='External')
        parts[word.RELS]=E.tostring(rels);data=pack(parts)
        refs=word.scan(data,[{'id':'a','identifier':'Annex 66','title':'Training','aliases':[]}])['references']
        result=word.add_links(data,refs,{'a':'Annex 66.pdf'});_,newrows=word.paragraphs(word.package(result));new=newrows[0][-1]
        self.assertEqual(word.text_of(p),word.text_of(new))
        for name in ('br','tab','noBreakHyphen','softHyphen','lastRenderedPageBreak'):
            self.assertEqual(len(new.findall('.//w:'+name,word.NS)),1)
        self.assertEqual([word.text_of(x) for x in new.findall('w:hyperlink',word.NS) if x.get(f'{{{word.R}}}id')=='old'],['before ', ', para. 1; after'])
