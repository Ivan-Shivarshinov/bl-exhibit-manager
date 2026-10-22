"""Real Office geometry with controlled annotation loss; fictional inputs only."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject
from exhibit import main_pdf
from exhibit.project import Store
from scripts.verify_feedback_office import create_review


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--executable')
    args = parser.parse_args()
    output = Path(args.output).resolve(); output.mkdir(parents=True, exist_ok=True)
    engine = ({'available':True, 'engine':'libreoffice', 'label':'LibreOffice', 'executable':args.executable}
              if args.executable else main_pdf.available())
    export = main_pdf.office_export; portable = main_pdf.portable_links
    report = {'engine':engine, 'controlled_loss':True, 'removed_annotations':0}

    def lose_annotations(source, target, directory, engine):
        export(source, target, directory, engine)
        if target.suffix != '.pdf': return
        writer = PdfWriter(clone_from=PdfReader(target))
        for page in writer.pages:
            kept = []
            for ref in page.get('/Annots', []):
                action = ref.get_object().get('/A', {})
                if str(action.get('/URI', '')).startswith('https://exhibit.invalid/'):
                    report['removed_annotations'] += 1
                else: kept.append(ref)
            page[NameObject('/Annots')] = ArrayObject(kept)
        out = BytesIO(); writer.write(out); target.write_bytes(out.getvalue())

    def check(data, markers, source):
        (output/'missing-links.pdf').write_bytes(data)
        try: portable(data, markers)
        except ValueError as error:
            assert 'потеряны ссылки' in str(error)
        else: raise AssertionError('Negative control did not detect lost links')
        result = portable(data, markers, source)
        old, new = PdfReader(BytesIO(data)), PdfReader(BytesIO(result))
        assert len(old.pages) == len(new.pages)
        for a, b in zip(old.pages, new.pages):
            assert a.get_contents().get_data() == b.get_contents().get_data()
        report.update(expected_links=len(markers), content_streams_unchanged=True)
        return result

    with TemporaryDirectory() as folder, patch.object(main_pdf, 'available', return_value=engine):
        store = Store(folder); project = create_review(store)
        original = store.source(project, project['main'])
        with patch.object(main_pdf, 'office_export', side_effect=lose_annotations), \
             patch.object(main_pdf, 'portable_links', side_effect=check):
            data = store.export(project)
        assert store.source(project, project['main']) == original
        with ZipFile(BytesIO(data)) as archive:
            reader = PdfReader(BytesIO(archive.read('Submission/Main document.pdf')))
            targets = []
            for page in reader.pages:
                for ref in page.get('/Annots', []):
                    action = ref.get_object().get('/A', {})
                    if action.get('/S') == '/GoToR':
                        target = str(action['/F']['/UF']); targets.append(target)
                        assert 'Submission/'+target in archive.namelist()
            assert len(set(targets)) == report['expected_links']
        (output/'Submission.zip').write_bytes(data)
        report.update(source_unchanged=True, all_targets_in_zip=True)
    (output/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
