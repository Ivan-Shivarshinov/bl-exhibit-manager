from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from exhibit import volumes, volume_settings


def sample(text='SOURCE', pages=1, form=False, rotation=0):
    out=BytesIO();c=canvas.Canvas(out,pagesize=(400,500),invariant=1)
    for n in range(pages):
        c.drawString(50,250,f'{text}-{n+1}')
        c.linkURL('https://example.com/source', (45,240,180,265),relative=0)
        if form:c.acroForm.textfield(name='name',value='REVIEWED',x=50,y=150,width=200,height=25)
        c.showPage()
    c.save();data=out.getvalue()
    if rotation:
        w=PdfWriter(clone_from=BytesIO(data))
        for p in w.pages:p.rotate(rotation)
        out=BytesIO();w.write(out);data=out.getvalue()
    return data


def item(n,pages=1,**kw):
    data=sample('SOURCE-'+str(n),pages,**kw)
    return {'id':f'{n:032x}','title':'Название # '+str(n),'identifier':'Annex '+str(n),
            'path':f'Папка #/Annex {n}.pdf','data':data,'revision':sha256(data).hexdigest(),
            'source_map':[{'part':'original','source_sha256':sha256(data).hexdigest(),'source_page':i+1,
                           'rect':None,'occurrence':1,'prepared_page':i+1} for i in range(pages)]}


class Volumes(unittest.TestCase):
    def config(self,**changes):return {**deepcopy(volume_settings.DEFAULT),'enabled':True,**changes}

    def test_duplicate_named_destinations_keep_local_and_unrelated_remote_targets(self):
        from pypdf.generic import DictionaryObject,NameObject,TextStringObject,ArrayObject,NumberObject
        items=[item(1),item(2)]
        for source in items:
            writer=PdfWriter(clone_from=BytesIO(source['data']));writer.add_named_destination('same',0)
            rect=ArrayObject([NumberObject(x) for x in (40,100,180,120)])
            ann=writer.add_annotation(0,DictionaryObject({NameObject('/Subtype'):NameObject('/Link'),NameObject('/Rect'):rect}))
            ann[NameObject('/Dest')]=TextStringObject('same')
            writer.add_annotation(0,DictionaryObject({NameObject('/Subtype'):NameObject('/Link'),NameObject('/Rect'):rect,
                NameObject('/A'):DictionaryObject({NameObject('/S'):NameObject('/GoToR'),NameObject('/F'):TextStringObject('Unrelated.pdf'),NameObject('/D'):TextStringObject('same')})}))
            raw=BytesIO();writer.write(raw);source['data']=raw.getvalue()
        files,mapping=volumes.build(items,self.config())
        reader=PdfReader(BytesIO(files['Volume 01.pdf']))
        self.assertEqual(len(reader.named_destinations),2)
        self.assertEqual(len(set(reader.named_destinations)),2)
        local=[a.get_object()['/Dest'] for p in reader.pages for a in p.get('/Annots',[]) if isinstance(a.get_object().get('/Dest'),str)]
        self.assertEqual(len(local),2);self.assertTrue(all(str(d) in reader.named_destinations for d in local))
        remote=[a.get_object()['/A'] for p in reader.pages for a in p.get('/Annots',[]) if a.get_object().get('/A',{}).get('/F')=='Unrelated.pdf']
        self.assertEqual(len(remote),2);self.assertTrue(all(a['/D']=='same' for a in remote))

    def test_safe_band_all_quarter_turns_preserves_source_and_widget_bounds(self):
        for rotation in (0,90,180,270):
            with self.subTest(rotation=rotation):
                source=item(1,form=True,rotation=rotation);original=PdfReader(BytesIO(source['data'])).pages[0]
                widget=next(a.get_object() for a in original['/Annots'] if a.get_object().get('/Subtype')=='/Widget')
                files,m=volumes.build([source],self.config(dividers=False))
                page=PdfReader(BytesIO(files['Volume 01.pdf'])).pages[1]
                saved=next(a.get_object() for a in page['/Annots'] if a.get_object().get('/Subtype')=='/Widget')
                self.assertEqual(saved['/Rect'],widget['/Rect']);self.assertEqual(page.rotation,rotation)
                self.assertIn('SOURCE-1-1',page.extract_text())
                from exhibit import pdf
                self.assertTrue(pdf.render_png(files['Volume 01.pdf'],2).startswith(b'\x89PNG'))

    def test_overlay_number_and_return_are_inside_crop_and_source_raster_survives_band(self):
        import pypdfium2 as pdfium
        from PIL import Image,ImageChops
        from exhibit import pdf
        for position in ('top-right','bottom-right','bottom-center'):
            files,m=volumes.build([item(1)],self.config(position=position,start=123456,dividers=False))
            with pdfium.PdfDocument(files['Volume 01.pdf']) as document:
                page=document[1];text=page.get_textpage();value=text.get_text_range();start=value.index('123457')
                for i in range(start,start+6):
                    left,bottom,right,top=text.get_charbox(i)
                    self.assertGreaterEqual(left,0);self.assertLessEqual(right,page.get_width())
                text.close();page.close()
        for rotation in (0,90,180,270):
            source=item(1,form=True,rotation=rotation)
            files,m=volumes.build([source],self.config(dividers=False))
            original=Image.open(BytesIO(pdf.render_png(source['data'],1,scale=1))).convert('RGB')
            result=Image.open(BytesIO(pdf.render_png(files['Volume 01.pdf'],2,scale=1))).convert('RGB')
            self.assertEqual(result.size,(original.width,original.height+30))
            self.assertIsNone(ImageChops.difference(original,result.crop((0,30,result.width,result.height))).getbbox(),rotation)

    def test_two_forms_with_same_field_name_stay_independent(self):
        files,m=volumes.build([item(1,form=True),item(2,form=True)],self.config())
        reader=PdfReader(BytesIO(files['Volume 01.pdf']))
        self.assertEqual(len(reader.get_fields()),2)
        self.assertTrue(all(f.get('/V')=='REVIEWED' for f in reader.get_fields().values()))

    def test_calculated_form_refused_instead_of_breaking_field_name_references(self):
        from pypdf.generic import ArrayObject,NameObject
        source=item(1,form=True);writer=PdfWriter(clone_from=BytesIO(source['data']))
        form=writer.root_object['/AcroForm'];form[NameObject('/CO')]=ArrayObject([form['/Fields'][0]])
        raw=BytesIO();writer.write(raw);source['data']=raw.getvalue();before=source['data']
        with self.assertRaisesRegex(ValueError,'вычислениями'):volumes.build([source],self.config())
        self.assertEqual(source['data'],before)

    def test_single_volume_map_toc_bookmarks_sources_and_unrelated_links(self):
        items=[item(1,2),item(2)];before=[i['data'] for i in items]
        files,m=volumes.build(items,self.config(start=12))
        reader=PdfReader(BytesIO(files['Volume 01.pdf']))
        self.assertEqual(len(reader.pages),6)
        self.assertEqual([r['local_page'] for r in m['pages']],[3,4,6])
        self.assertEqual([r['global_number'] for r in m['pages']],[14,15,17])
        self.assertEqual(reader.page_labels[0],'12')
        self.assertIn('Название # 1',reader.pages[0].extract_text())
        self.assertIn('SOURCE-1-1',reader.pages[2].extract_text())
        self.assertGreaterEqual(len(reader.outline),3)
        self.assertEqual([i['data'] for i in items],before)
        urls=[str(a.get_object().get('/A',{}).get('/URI','')) for p in reader.pages for a in p.get('/Annots',[])]
        self.assertIn('https://example.com/source',urls)
        volumes.verify(files,m)

    def test_multiple_volumes_exact_page_targets_and_relative_files(self):
        a,b,c=item(1,2),item(2,2),item(3,2)
        files,m=volumes.build([a,b,c],self.config(max_pages=4))
        self.assertEqual(len(files),3)
        self.assertEqual([v['pages'] for v in m['volumes']],[4,4,4])
        self.assertEqual([(r['volume'],r['local_page']) for r in m['pages']],[(1,3),(1,4),(2,3),(2,4),(3,3),(3,4)])
        reader=PdfReader(BytesIO(files['Volume 01.pdf']))
        actions=[a.get_object()['/A'] for a in reader.pages[0]['/Annots'] if a.get_object().get('/BLVolumeLink')]
        remote=[a for a in actions if a['/S']=='/GoToR']
        self.assertEqual({str(a['/F']['/UF']) for a in remote},{'Volume 02.pdf','Volume 03.pdf'})
        self.assertTrue(all(a['/D'][0]==2 for a in remote))

    def test_forms_canonical_fields_widgets_and_rotation(self):
        files,m=volumes.build([item(1,form=True),item(2,rotation=90)],self.config())
        reader=PdfReader(BytesIO(next(iter(files.values()))))
        values=[f.get('/V') for f in reader.get_fields().values()]
        self.assertIn('REVIEWED',values)
        widgets=[a.get_object() for p in reader.pages for a in p.get('/Annots',[]) if a.get_object().get('/Subtype')=='/Widget']
        self.assertEqual(len(widgets),1)
        self.assertEqual(widgets[0].get('/V',widgets[0].get('/Parent',{}).get('/V')),'REVIEWED')
        rotated=reader.pages[-1];self.assertEqual(rotated.rotation,90)
        self.assertEqual(float(rotated.cropbox.width),430)

    def test_many_rows_consistent_index_pages(self):
        items=[item(n) for n in range(1,65)]
        files,m=volumes.build(items,self.config(dividers=False))
        reader=PdfReader(BytesIO(next(iter(files.values()))))
        first=m['pages'][0]['local_page']
        self.assertGreater(first,2)
        self.assertEqual(len(reader.pages),first-1+64)
        for r in m['pages']:
            self.assertIn('SOURCE-'+str(int(r['document_id'],16))+'-1',reader.pages[r['local_page']-1].extract_text())

    def test_cancel_and_oversize_never_return_partial_result(self):
        with self.assertRaisesRegex(ValueError,'отменена'):volumes.build([item(1)],self.config(),stop=lambda:True)
        with self.assertRaisesRegex(ValueError,'предел страниц'):volumes.build([item(1,3)],self.config(max_pages=3))
        with self.assertRaisesRegex(ValueError,'Карта источника'):
            a=item(1);a['source_map']=[];volumes.build([a],self.config())

    def test_source_map_translation_fragments_and_repeated_pages(self):
        doc={'mode':'prepare','original':{'pages':3,'sha256':'a'*64},'translation':{'pages':1,'sha256':'b'*64},
             'translation_selection':[{'page':1}], 'selection':[{'page':2,'rect':[0,0,.5,1]},{'page':2,'rect':[.5,0,1,1]},{'page':1}]}
        rows=volumes.source_pages(doc)
        self.assertEqual([r['part'] for r in rows],['translation','original','original','original'])
        self.assertEqual([r['prepared_page'] for r in rows],[1,2,3,4])
        self.assertEqual(rows[2]['occurrence'],2)
        self.assertEqual(rows[2]['rect'],[.5,0,1,1])

    def test_settings_and_schema_backup_restore(self):
        from exhibit.project import Store
        from exhibit import backup
        with tempfile.TemporaryDirectory() as temp:
            store=Store(temp);p=store.create('Проверка');store.upload(p,'Original.pdf',sample(),mode='passthrough')
            original=store.source(p,p['documents'][0]['original'])
            old_schema=p['schema'];p=volume_settings.save(store,p,self.config())
            self.assertEqual(p['schema'],4);self.assertTrue((Path(temp)/p['schema_backup']).is_file())
            self.assertEqual(store.source(p,p['documents'][0]['original']),original)
            path=Path(temp)/'backup.zip';backup.save_archive(store,p['id'],path)
            self.assertEqual(backup.inspect_archive(path)['project']['volume_settings'],p['volume_settings'])
            with self.assertRaises(ValueError):volume_settings.validate({**self.config(),'start':True})
            with self.assertRaises(ValueError):volume_settings.validate({**self.config(),'number_format':'{wrong}'})
            from exhibit.schema import check_version
            with self.assertRaisesRegex(ValueError,'не поддерживается'):check_version(5)
            self.assertLess(old_schema,4)

    def test_signed_original_remains_unchanged_and_unsupported_forms_fail(self):
        from tests.pdf_forms import form_pdf
        for kind in ('signed','widget','perms'):
            raw=form_pdf(kind);a=item(1,2);a['data']=raw;a['revision']=sha256(raw).hexdigest()
            for r in a['source_map']:r['source_sha256']=sha256(raw).hexdigest()
            if kind=='signed':
                files,m=volumes.build([a],self.config())
                self.assertTrue(PdfReader(BytesIO(next(iter(files.values())))).get_fields())
            else:
                with self.assertRaisesRegex(ValueError,'Нельзя безопасно перенести'):volumes.build([a],self.config())
            self.assertEqual(a['data'],raw)

    def test_actual_byte_limit_splits_and_rejects_a_single_large_item(self):
        from PIL import Image
        from reportlab.lib.utils import ImageReader
        import random
        def picture(n,size):
            image=Image.frombytes('RGB',(size,size),random.Random(n).randbytes(size*size*3))
            output=BytesIO();c=canvas.Canvas(output,pagesize=(600,700),invariant=1)
            c.drawImage(ImageReader(image),50,80,500,500);c.showPage();c.save()
            a=item(n);a['data']=output.getvalue();a['revision']=sha256(a['data']).hexdigest();return a
        files,m=volumes.build([picture(1,420),picture(2,420)],self.config(max_mb=1))
        self.assertEqual(len(files),2);self.assertTrue(all(len(raw)<=1_000_000 for raw in files.values()))
        with self.assertRaisesRegex(ValueError,'предел размера'):volumes.build([picture(3,800)],self.config(max_mb=1))

    def test_overlay_collision_is_blocked_but_safe_band_preserves_content(self):
        out=BytesIO();c=canvas.Canvas(out,pagesize=(400,500),invariant=1);c.rect(270,470,125,28,fill=1);c.showPage();c.save()
        a=item(1);a['data']=out.getvalue();a['revision']=sha256(a['data']).hexdigest()
        with self.assertRaisesRegex(ValueError,'перекрывает'):volumes.build([a],self.config(dividers=False,position='top-right'))
        files,_=volumes.build([a],self.config(dividers=False,position='band'))
        self.assertEqual(len(PdfReader(BytesIO(next(iter(files.values())))).pages),2)


if __name__=='__main__':unittest.main()
