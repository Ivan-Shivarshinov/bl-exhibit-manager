"""Validate the actual XML emitted by the taskpane, not a duplicate generator."""
import json
from pathlib import Path
import shutil
import subprocess
from unittest import TestCase, skipUnless

from lxml import etree as E


NODE = shutil.which('node')
ROOT = Path(__file__).resolve().parents[1]
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'rel': 'http://schemas.openxmlformats.org/package/2006/relationships'}


@skipUnless(NODE, 'Node.js is required to test taskpane OOXML')
class CitationOoxml(TestCase):
    def package(self, *args):
        script = "import {citationOoxml} from './web/public/word/citation-ooxml.js'; console.log(citationOoxml(...JSON.parse(process.argv[1])));"
        result = subprocess.run([NODE, '--input-type=module', '-e', script, json.dumps(args)],
                                cwd=ROOT, capture_output=True, check=True)
        return E.fromstring(result.stdout)

    def test_locator_and_separator_are_outside_link(self):
        root = self.package('Annex 1, Code', 'Annex 1', 'https://localhost/doc', '; ', ', paras. 5–6')
        link = root.find('.//w:hyperlink', NS)
        self.assertEqual(link.xpath('.//w:t/text()', namespaces=NS), ['Annex 1', ', Code'])
        self.assertEqual(link.getprevious().find('w:t', NS).text, '; ')
        self.assertEqual(link.getnext().find('w:t', NS).text, ', paras. 5–6')
        self.assertEqual(len(root.findall('.//w:hyperlink', NS)), 1)
        self.assertEqual(link.xpath('w:r/w:rPr/w:b/@w:val', namespaces=NS), ['1', '0'])
        self.assertEqual(link.xpath('w:r/w:rPr/w:u/@w:val', namespaces=NS), ['none', 'none'])
        # Do not force italic off in a footnote whose paragraph style is italic.
        self.assertEqual(link.findall('.//w:i', NS), [])

    def test_unicode_and_xml_characters_round_trip_in_text_and_relationship(self):
        title = 'Annex 2, Кодекс <draft> & "final" / ' + 'long title ' * 50
        address = 'https://localhost/document?text="Code"&form=full'
        root = self.package(title, 'Annex 2', address)
        link = root.find('.//w:hyperlink', NS)
        self.assertEqual(''.join(link.xpath('.//w:t/text()', namespaces=NS)), title)
        rid = link.get('{'+NS['r']+'}id')
        rel = root.find('.//rel:Relationship[@Id="'+rid+'"]', NS)
        self.assertEqual(rel.get('Target'), address)
        self.assertEqual(rel.get('TargetMode'), 'External')

    def test_short_title_without_identifier_is_not_bold(self):
        root = self.package('Code', 'RLA-1', 'https://localhost/document')
        link = root.find('.//w:hyperlink', NS)
        self.assertEqual(link.xpath('.//w:t/text()', namespaces=NS), ['Code'])
        self.assertEqual(link.xpath('w:r/w:rPr/w:b/@w:val', namespaces=NS), ['0'])
