from unittest import TestCase
from lxml import etree as E
from exhibit import word, word_bridge

PKG = 'http://schemas.microsoft.com/office/2006/xmlPackage'


def fragment():
    root = E.Element(f'{{{PKG}}}package', nsmap={'pkg': PKG})
    part = E.SubElement(root, f'{{{PKG}}}part', {f'{{{PKG}}}name': '/word/document.xml'})
    link = E.SubElement(E.SubElement(part, f'{{{PKG}}}xmlData'), f'{{{word.W}}}hyperlink', {f'{{{word.R}}}id': 'r1'})
    for text, style in [('Annex 1', 'b'), (', Old title', 'i')]:
        run = E.SubElement(link, f'{{{word.W}}}r')
        props = E.SubElement(run, f'{{{word.W}}}rPr')
        E.SubElement(props, f'{{{word.W}}}{style}')
        E.SubElement(props, f'{{{word.W}}}rFonts', {f'{{{word.W}}}ascii': 'Times New Roman'})
        E.SubElement(run, f'{{{word.W}}}t').text = text
    part = E.SubElement(root, f'{{{PKG}}}part', {f'{{{PKG}}}name': '/word/_rels/document.xml.rels'})
    E.SubElement(E.SubElement(part, f'{{{PKG}}}xmlData'), f'{{{word.REL}}}Relationship', Id='r1', Target='old')
    return root


class WordRewrite(TestCase):
    def rewrite(self, root, text='Annex 2, New title'):
        return word.xml(word_bridge.rewrite_citation_ooxml(
            E.tostring(root, encoding='unicode'), 'Annex 1, Old title', text,
            word_bridge.link('https://localhost:8769', 'a'*32, 'b'*32, text), 'Annex 2').encode())

    def test_renumber_preserves_bold_identifier_and_italic_title_font(self):
        result = self.rewrite(fragment())
        runs = result.findall('.//w:r', word.NS)
        self.assertEqual([word.text_of(r) for r in runs], ['Annex 2', ', New title'])
        self.assertIsNotNone(runs[0].find('w:rPr/w:b', word.NS))
        self.assertIsNotNone(runs[1].find('w:rPr/w:i', word.NS))
        self.assertIsNone(runs[1].find('w:rPr/w:b', word.NS))
        self.assertEqual(runs[1].find('w:rPr/w:rFonts', word.NS).get(f'{{{word.W}}}ascii'), 'Times New Roman')

    def test_mixed_title_rejected_and_source_unchanged(self):
        root = fragment(); link = root.find('.//w:hyperlink', word.NS)
        link[-1].find('w:t', word.NS).text = ', Old '
        run = E.SubElement(link, f'{{{word.W}}}r')
        E.SubElement(run, f'{{{word.W}}}t').text = 'title'
        before = E.tostring(root)
        with self.assertRaisesRegex(ValueError, 'смешанное оформление'): self.rewrite(root)
        self.assertEqual(E.tostring(root), before)
        # Changing only the address does not require changing mixed runs.
        result = self.rewrite(root, 'Annex 1, Old title')
        self.assertEqual(E.tostring(result.find('.//w:hyperlink', word.NS)), E.tostring(link))

    def test_stale_and_malformed_fragment_refused(self):
        root = fragment(); root.find('.//w:t', word.NS).text = 'Manual edit'
        with self.assertRaisesRegex(ValueError, 'изменился'): self.rewrite(root)
        with self.assertRaises(ValueError):
            word_bridge.rewrite_citation_ooxml('<broken', 'old', 'new', word_bridge.link('https://localhost:8769', 'a'*32, 'b'*32, 'new'))
