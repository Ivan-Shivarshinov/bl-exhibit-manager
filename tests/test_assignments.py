from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile,ZIP_DEFLATED
from lxml import etree as E
from pypdf import PdfReader
from exhibit import assignments,word,word_bridge,history,volume_settings,backup
from exhibit.project import Store,identifier
from tests.test_feedback import citation_docx
from tests.test_volumes import sample


def pack(parts):
    out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as z:
        for n,data in parts.items():z.writestr(n,data)
    return out.getvalue()


class Assignments(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.s=Store(self.temp.name);self.p=self.s.create('Assignments')
        for n,title in [(1,'First source'),(2,'Second source')]:
            self.s.upload(self.p,f'file{n}.pdf',sample('SOURCE'+str(n),2))
            did=self.p['documents'][-1]['id'];self.s.update(self.p,did,{'designation':'Annex','number':n,'title':title});self.s.approve(self.p,did,'document')
        self.ids=[d['id'] for d in self.p['documents']]
        self.oldzip=self.s.export(self.p)
        self.s.upload(self.p,'Main.docx',citation_docx(['Annex 1, First source, p. 2.','Annex 2, Second source, para. 4.']),'main')
        self.s.scan(self.p);self.s.confirm_links(self.p)

    def request(self,decide=True):
        return {'rows':[{'id':self.ids[0],'number':2},{'id':self.ids[1],'number':1}], 'naming':'identifier',
                'references':{r['key']:'update' for r in self.p['references']} if decide else {}}

    def test_swap_updates_known_docx_and_pdf_but_preserves_original_history_and_other_project(self):
        other=self.s.create('Independent');self.s.upload(other,'Ready.pdf',sample(),mode='passthrough');other_before=deepcopy(other)
        original_main=deepcopy(self.p['main']);raw=self.s.source(self.p,original_main)
        req=self.request();proposed,data,report=assignments.plan(self.s,self.p['id'],req)
        self.assertFalse(report['conflicts'],report)
        self.assertEqual([identifier(d,proposed) for d in proposed['documents']],['Annex 2','Annex 1'])
        p=assignments.apply(self.s,self.p['id'],req,report['token'])
        text=' '.join(word.text_of(row[3]) for row in word.paragraphs(word.package(self.s.source(p,p['main'])))[1])
        self.assertIn('Annex 2, First source, p. 2.',text);self.assertIn('Annex 1, Second source, para. 4.',text)
        self.assertEqual(self.s.source(p,original_main),raw);self.assertNotEqual(p['main']['sha256'],original_main['sha256'])
        self.assertEqual(self.s.load(other['id']),other_before)
        self.assertEqual(history.read_export(self.s,p,history.list_exports(self.s,p)[0]['id']),self.oldzip)
        self.assertFalse(p['links_reviewed']);self.assertEqual({r['target'] for r in p['references']},set(self.ids))
        self.s.confirm_links(p)
        for d in p['documents']:self.s.approve(p,d['id'],'document')
        p=volume_settings.save(self.s,p,{**deepcopy(volume_settings.DEFAULT),'enabled':True})
        from exhibit.volume_jobs import manager
        service=manager(self.s);self.addCleanup(service.close);model=service.preview(p['id']);result=service.export_bytes(p['id'],model['preview_token'])
        with ZipFile(BytesIO(result)) as z:
            reader=PdfReader(BytesIO(z.read('Submission/Annex 2.pdf')))
            self.assertTrue(all('Annex 2' in page.extract_text() for page in reader.pages))
            self.assertIn('Submission/Volumes/Volume 01.pdf',z.namelist())
        path=Path(self.temp.name)/'copy.zip';backup.save_archive(self.s,p['id'],path)
        restored=backup.inspect_archive(path)['project'];self.assertEqual(restored['assignment_sources'][0],original_main)
        with TemporaryDirectory() as destination:
            target=Store(destination);copied=backup.restore_archive(target,path,copy=True)
            self.assertEqual(target.source(copied,copied['main']),self.s.source(p,p['main']))
            self.assertEqual(target.source(copied,copied['assignment_sources'][0]),raw)

    def test_unmanaged_decisions_conflicts_stale_and_no_partial_write(self):
        file=self.s.folder(self.p['id'])/'project.json';before=file.read_bytes()
        _,_,report=assignments.plan(self.s,self.p['id'],self.request(False));self.assertTrue(report['conflicts']);self.assertEqual(file.read_bytes(),before)
        req=self.request();_,_,report=assignments.plan(self.s,self.p['id'],req)
        self.s.update(self.p,self.ids[0],{'title':'Changed'})
        current=file.read_bytes()
        with self.assertRaisesRegex(ValueError,'изменились'):assignments.apply(self.s,self.p['id'],req,report['token'])
        self.assertEqual(file.read_bytes(),current)
        req={'rows':[{'id':self.ids[0],'number':2}], 'naming':'keep','references':{}}
        _,_,report=assignments.plan(self.s,self.p['id'],req);self.assertTrue(any(c['code']=='duplicate_id' for c in report['conflicts']))

    def test_manual_filename_is_preserved_until_explicit_change(self):
        self.s.update(self.p,self.ids[0],{'filename':'Manual kept.pdf'})
        self.s.scan(self.p);self.s.confirm_links(self.p)
        req=self.request();_,_,report=assignments.plan(self.s,self.p['id'],req)
        p=assignments.apply(self.s,self.p['id'],req,report['token'])
        self.assertEqual(p['documents'][0]['filename'],'Manual kept.pdf')
        self.assertEqual(p['documents'][1]['filename'],'Annex 1.pdf')

    def test_mixed_typography_locator_and_unrelated_url_survive(self):
        parts=word.package(self.s.source(self.p,self.p['main']));root=word.xml(parts[word.FOOT])
        para=root.find("w:footnote[@w:id='1']/w:p",word.NS);para[:]=[]
        for text,style in [('Annex 1','b'),(', First source','i'),(', p. 2.','i')]:
            run=E.SubElement(para,f'{{{word.W}}}r');props=E.SubElement(run,f'{{{word.W}}}rPr');E.SubElement(props,f'{{{word.W}}}{style}');E.SubElement(run,f'{{{word.W}}}t').text=text
        rels=word.xml(parts[word.RELS]) if word.RELS in parts else E.Element(f'{{{word.REL}}}Relationships')
        E.SubElement(rels,f'{{{word.REL}}}Relationship',Id='rIdUnrelated',Type=word.R+'/hyperlink',Target='https://example.com/unchanged',TargetMode='External')
        h=E.SubElement(para,f'{{{word.W}}}hyperlink',{f'{{{word.R}}}id':'rIdUnrelated'});E.SubElement(E.SubElement(h,f'{{{word.W}}}r'),f'{{{word.W}}}t').text=' unrelated web link'
        parts[word.FOOT]=E.tostring(root);parts[word.RELS]=E.tostring(rels)
        self.s.upload(self.p,'Mixed.docx',pack(parts),'main');self.s.scan(self.p);self.s.confirm_links(self.p)
        req=self.request();_,_,report=assignments.plan(self.s,self.p['id'],req)
        self.assertFalse(report['conflicts'],report)
        p=assignments.apply(self.s,self.p['id'],req,report['token']);after=word.package(self.s.source(p,p['main']))
        note=word.xml(after[word.FOOT]).find("w:footnote[@w:id='1']/w:p",word.NS)
        self.assertEqual(word.text_of(note),'Annex 2, First source, p. 2. unrelated web link')
        runs=note.findall('w:r',word.NS)
        self.assertIsNotNone(runs[0].find('w:rPr/w:b',word.NS));self.assertIsNotNone(runs[1].find('w:rPr/w:i',word.NS))
        self.assertIn(b'https://example.com/unchanged',after[word.RELS])

    def test_known_managed_links_update_automatically_and_remain_managed(self):
        data,paths=self.s.linked_main(self.p);parts=word.package(data);rels=word.xml(parts[word.RELS])
        for rel in rels:
            if not rel.get('Type','').endswith('/hyperlink'):continue
            did=next(d['id'] for d in self.p['documents'] if rel.get('Target')==d['filename'])
            ref=next(r for r in self.p['references'] if r['target']==did)
            rel.set('Target',word_bridge.link('https://localhost:8769',self.p['id'],did,ref['link_text']))
        parts[word.RELS]=E.tostring(rels);self.s.upload(self.p,'Managed.docx',pack(parts),'main');self.s.scan(self.p);self.s.confirm_links(self.p)
        req=self.request(False);_,_,report=assignments.plan(self.s,self.p['id'],req)
        self.assertFalse(report['conflicts'],report);self.assertTrue(report['word_pending'])
        p=assignments.apply(self.s,self.p['id'],req,report['token'])
        self.assertTrue(all(r.get('managed') and r['mention']==r['managed']['expected'] for r in p['references']))
        self.assertTrue(p['assignment_operations'][-1]['word_pending'])

    def test_unsupported_field_refuses_before_writing_and_keep_is_explicit(self):
        parts=word.package(self.s.source(self.p,self.p['main']));root=word.xml(parts[word.FOOT]);para=word.paragraphs(parts)[1][0][3]
        # Inject a field in the first actual note rather than removing it to pass.
        note=root.find("w:footnote[@w:id='1']/w:p",word.NS)
        field=E.SubElement(note,f'{{{word.W}}}fldSimple');field.set(f'{{{word.W}}}instr','PAGE')
        parts[word.FOOT]=E.tostring(root);self.s.upload(self.p,'Field.docx',pack(parts),'main');self.s.scan(self.p)
        before=(self.s.folder(self.p['id'])/'project.json').read_bytes();req=self.request()
        _,_,report=assignments.plan(self.s,self.p['id'],req)
        self.assertTrue(any(v['code']=='rewrite' for v in report['conflicts']))
        with self.assertRaises(ValueError):assignments.apply(self.s,self.p['id'],req,report['token'])
        self.assertEqual((self.s.folder(self.p['id'])/'project.json').read_bytes(),before)

    def test_api_preview_apply_and_repeated_token(self):
        from exhibit import app
        from tests.api_client import TestClient
        with patch.object(app,'store',self.s):
            client=TestClient(app.app,base_url='http://localhost');headers={'X-Exhibit-Local':'1'};base='/api/projects/'+self.p['id']+'/assignments/'
            req=self.request();preview=client.post(base+'preview',json={'request':req},headers=headers)
            self.assertEqual(preview.status_code,200,preview.text)
            body={'request':req,'token':preview.json()['token']}
            result=client.post(base+'apply',json=body,headers=headers)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()['schema'],4)
            self.assertEqual(client.post(base+'apply',json=body,headers=headers).status_code,400)


if __name__=='__main__':__import__('unittest').main()
