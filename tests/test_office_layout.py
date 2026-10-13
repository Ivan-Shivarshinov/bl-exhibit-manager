from io import BytesIO
import unittest
from unittest.mock import patch
from lxml import etree as E
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, DecodedStreamObject
from exhibit import office_layout, font_diagnostics, word
from scripts.layout_probe import footnotes, pack


def tagged_pdf(continued=False, extra_path=False, missing_note=False):
    writer=PdfWriter();writer.add_blank_page(612,792)
    if continued:writer.add_blank_page(612,792)
    page=writer.pages[-1]
    stream=DecodedStreamObject()
    text=b'/Note << /MCID 1 >> BDC EMC /Artifact BMC 0.035294118 0.12941176 0.21568627 RG 0.035294118 0.12941176 0.21568627 rg 72 100 m 216 100 l 216 100.5 l 72 100.5 l 72 100 l h B* EMC '
    if extra_path:text+=b'/Artifact BMC 0 0 0 RG 72 250 m 216 250 l S EMC'
    stream.set_data(text);page[NameObject('/Contents')]=writer._add_object(stream)
    if not missing_note:
        note=DictionaryObject({NameObject('/Type'):NameObject('/StructElem'),NameObject('/S'):NameObject('/Note'),NameObject('/Pg'):writer.pages[0].indirect_reference,
            NameObject('/K'):DictionaryObject({NameObject('/Type'):NameObject('/MCR'),NameObject('/Pg'):page.indirect_reference,NameObject('/MCID'):NumberObject(1)})})
        writer._root_object[NameObject('/StructTreeRoot')]=DictionaryObject({NameObject('/K'):ArrayObject([writer._add_object(note)])})
    out=BytesIO();writer.write(out);return out.getvalue()


class OfficeLayout(unittest.TestCase):
    tags={'#092137':{'width':468,'original':'#000000'}}
    def test_only_tagged_rule_moves_and_continuation_reaches_right_margin(self):
        for continued in (False,True):
            result=office_layout.restore_rules(tagged_pdf(continued,True),self.tags,{'separator':35.45,'continuationSeparator':24})
            ops=PdfReader(BytesIO(result)).pages[-1].get_contents().operations
            points=[v for v,op in ops if op in (b'm',b'l')]
            self.assertAlmostEqual(points[0][0],96 if continued else 107.45)
            self.assertAlmostEqual(points[1][0],540 if continued else 251.45)
            self.assertEqual(points[-2:],[ [72,250],[216,250]])
            self.assertTrue(all(list(v)==[0,0,0] for v,op in ops if op in (b'rg',b'RG')))

    def test_unproven_note_or_wrong_geometry_refuses_instead_of_guessing(self):
        with self.assertRaisesRegex(ValueError,'безопасно'):
            office_layout.restore_rules(tagged_pdf(missing_note=True),self.tags,{'separator':35.45,'continuationSeparator':0})
        with self.assertRaisesRegex(ValueError,'безопасно'):
            office_layout.restore_rules(tagged_pdf(),{'#111111':{'width':468,'original':'#000000'}},{'separator':35.45,'continuationSeparator':0})

    def test_inherited_indents_hanging_and_original_package_unchanged(self):
        source=footnotes();self.assertEqual(office_layout.separator_offsets(source),{'separator':35.45,'continuationSeparator':35.45})
        parts=word.package(source);before=parts.copy();root=word.xml(parts[word.FOOT]);p=root[0].find('w:p/w:pPr',word.NS)
        ind=p.find('w:ind',word.NS);ind.attrib.clear();ind.set('{'+word.W+'}hanging','120')
        E.SubElement(p,'{'+word.W+'}pStyle',{'{'+word.W+'}val':'Inset'})
        styles=word.xml(parts['word/styles.xml']);s=E.SubElement(styles,'{'+word.W+'}style',{'{'+word.W+'}styleId':'Inset','{'+word.W+'}type':'paragraph'})
        pr=E.SubElement(s,'{'+word.W+'}pPr');E.SubElement(pr,'{'+word.W+'}ind',{'{'+word.W+'}left':'480','{'+word.W+'}firstLine':'240'})
        parts[word.FOOT]=E.tostring(root);parts['word/styles.xml']=E.tostring(styles)
        self.assertEqual(office_layout.separator_offsets(pack(parts))['separator'],18)
        self.assertEqual(word.package(source),before)

    def test_font_warning_resolves_and_does_not_include_document_text(self):
        data=footnotes(font='Missing Test Family')
        with patch.object(font_diagnostics,'_inventory',return_value=(set(),True)):
            status=font_diagnostics.inspect(data,{'engine':'libreoffice'})
        self.assertEqual(status['missing'],['Missing Test Family']);self.assertIn('Установите',status['message'])
        self.assertNotIn('Synthetic main text',str(status))
        with patch.object(font_diagnostics,'_inventory',return_value=({'missingtestfamily'},True)):
            self.assertEqual(font_diagnostics.inspect(data,{'engine':'libreoffice'})['missing'],[])

    def test_pdf_family_confirmation_is_independent_of_installed_fonts(self):
        from reportlab.pdfgen import canvas
        out=BytesIO();c=canvas.Canvas(out);c.setFont('Times-Roman',12);c.drawString(72,700,'Synthetic text');c.save()
        result=font_diagnostics.pdf_report(out.getvalue(),['Times New Roman'])
        self.assertIn('Times-Roman',result['names']);self.assertEqual(result['unconfirmed'],['Times New Roman'])
        self.assertIn('В созданном PDF',result['message'])

    def test_fodt_marker_only_changes_rule_colour_and_avoids_existing_colour(self):
        data=b'''<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"><style:page-layout><style:page-layout-properties fo:page-width="8.5in" fo:margin-left="1in" fo:margin-right="1in"><style:footnote-sep style:adjustment="left" style:rel-width="25%" style:color="#092137"/></style:page-layout-properties></style:page-layout></office:document>'''
        tagged,tags=office_layout.tag_rules(data);self.assertNotIn('#092137',tags)
        self.assertEqual(next(iter(tags.values()))['width'],468)
        before,after=word.xml(data),word.xml(tagged)
        a=before.find('.//{'+office_layout.STYLE+'}footnote-sep');b=after.find('.//{'+office_layout.STYLE+'}footnote-sep');b.set('{'+office_layout.STYLE+'}color',a.get('{'+office_layout.STYLE+'}color'))
        self.assertEqual(E.tostring(before),E.tostring(after))
