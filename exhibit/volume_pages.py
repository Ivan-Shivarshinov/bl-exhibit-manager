"""PDF volume primitives. Original content and unrelated actions stay intact."""
from io import BytesIO
from copy import deepcopy
from pathlib import PurePosixPath
from collections import Counter

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DictionaryObject, NameObject, NumberObject,
                           TextStringObject, FloatObject, BooleanObject)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from . import pdf
from .index_documents import wrap,check_text

BAND = 30


def file_action(filename, page):
    return DictionaryObject({NameObject('/S'): NameObject('/GoToR'),
        NameObject('/F'): DictionaryObject({NameObject('/Type'): NameObject('/Filespec'),
            NameObject('/F'): TextStringObject(filename), NameObject('/UF'): TextStringObject(filename)}),
        NameObject('/D'): ArrayObject([NumberObject(page), NameObject('/Fit')]),
        NameObject('/NewWindow'): BooleanObject(False)})


def jump(writer, annotation, volume, destination, own_volume, files):
    annotation.pop(NameObject('/Dest'), None)
    if volume == own_volume:
        annotation[NameObject('/A')] = DictionaryObject({NameObject('/S'): NameObject('/GoTo'),
            NameObject('/D'): ArrayObject([writer.pages[destination].indirect_reference, NameObject('/Fit')])})
    else:
        annotation[NameObject('/A')] = file_action(files[volume], destination)
    annotation[NameObject('/BLVolumeLink')] = BooleanObject(True)
    annotation[NameObject('/Border')] = ArrayObject([NumberObject(0)]*3)


def link(rect, uri):
    return DictionaryObject({NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject('/Link'),
        NameObject('/Rect'): ArrayObject([FloatObject(x) for x in rect]),
        NameObject('/Border'): ArrayObject([NumberObject(0)]*3),
        NameObject('/A'): DictionaryObject({NameObject('/S'): NameObject('/URI'), NameObject('/URI'): TextStringObject(uri)})})


def index_pdf(rows, title, volume_number):
    """Return stable layout and hit rectangles, independent of final page refs."""
    pdf.init_font()
    check_text({'title':title,'language':'ru','columns':['pages']},rows)
    out = BytesIO(); c = canvas.Canvas(out, pagesize=A4, invariant=1)
    c.setTitle(title); c.setAuthor('BL Exhibit Manager')
    width, height = A4; page = -1; y = 0; boxes = []
    def header():
        nonlocal page, y
        if page >= 0: c.showPage()
        page += 1; y = height-48
        c.setFillColorRGB(.07,.16,.29); c.setFont('DejaVu', 13)
        for line in wrap(title, width-96, 13): c.drawString(48,y,line); y-=18
        c.setFont('DejaVu',8); c.drawString(48,y-2,f'Том {volume_number+1} · переходы ведут к указанным страницам')
        y-=28; c.setFont('DejaVu',9)
        c.drawString(48,y,'Приложение / документ'); c.drawString(width-160,y,'Том'); c.drawRightString(width-48,y,'Страницы'); y-=17
    header()
    for row in rows:
        text=(row['identifier']+' · ' if row['identifier'] else '')+row['title']
        lines=wrap(text,width-260,9); h=max(24,13*len(lines)+10)
        if h > height-150: raise ValueError('Название не помещается в перечень тома: '+row['title'])
        if y-h < 48: header()
        top=y+9
        c.setFont('DejaVu',9); c.setFillColorRGB(.07,.16,.29)
        for line in lines: c.drawString(48,y,line); y-=13
        c.drawString(width-160,top-9,str(row['volume']+1))
        c.drawRightString(width-48,top-9,f"{row['first']}–{row['last']}")
        bottom=top-h
        boxes.append({'page':page,'rect':(46,bottom,width-46,top),'id':row['id']})
        c.setStrokeColorRGB(.86,.89,.93); c.line(48,bottom,width-48,bottom)
        y=bottom-10
    c.save()
    return out.getvalue(), boxes


def divider_pdf(item):
    pdf.init_font(); out=BytesIO(); c=canvas.Canvas(out,pagesize=A4,invariant=1)
    c.setFillColorRGB(.07,.16,.29); c.setFont('DejaVu',22); y=A4[1]-150
    for line in wrap(item['identifier'] or 'Основной документ',A4[0]-100,22): c.drawString(50,y,line);y-=31
    c.setFont('DejaVu',14)
    for line in wrap(item['title'],A4[0]-100,14): c.drawString(50,y-20,line);y-=21
    c.save();return out.getvalue()


def namespace(reader, prefix):
    """Keep named destinations and form fields distinct across input files."""
    names={str(n):prefix+str(n) for n in reader.named_destinations}
    root=reader.trailer['/Root']
    def rename_tree(node):
        node=node.get_object()
        pairs=node.get('/Names',[])
        for i in range(0,len(pairs),2):
            if str(pairs[i]) in names:pairs[i]=TextStringObject(names[str(pairs[i])])
        for child in node.get('/Kids',[]):rename_tree(child)
    dests=root.get('/Names',{}).get_object() if root.get('/Names') else {}
    if dests.get('/Dests'):rename_tree(dests['/Dests'])
    if root.get('/Dests'):
        old=root['/Dests']; renamed=DictionaryObject()
        for key,value in old.items():renamed[NameObject('/'+names.get(str(key).lstrip('/'),prefix+str(key).lstrip('/')))]=value
        root[NameObject('/Dests')]=renamed
    visited=set()
    def walk(obj,depth=0):
        obj=obj.get_object() if hasattr(obj,'get_object') else obj
        if not isinstance(obj,(dict,list)) or id(obj) in visited:return
        if depth>100:raise ValueError('Слишком сложная структура PDF для тома.')
        visited.add(id(obj))
        if isinstance(obj,dict):
            for key in ('/Dest',*(['/D'] if obj.get('/S')=='/GoTo' else [])):
                value=obj.get(key)
                if isinstance(value,str) and str(value).lstrip('/') in names:
                    obj[NameObject(key)]=TextStringObject(names[str(value).lstrip('/')])
            for value in list(obj.values()):walk(value,depth+1)
        else:
            for value in obj:walk(value,depth+1)
    if names:walk(root)
    if reader.get_fields():
        # Rename existing top fields instead of inserting an artificial parent.
        # PDFium stops drawing some widgets under such a synthetic field group.
        form=root['/AcroForm']
        for index,entry in enumerate(form['/Fields']):
            field=entry.get_object()
            field[NameObject('/T')]=TextStringObject(prefix.rstrip('.')+'_'+str(field.get('/T',index)))


def stamp_number(writer, index, label, position):
    """Add a blank visual top band without translating original PDF objects."""
    page=writer.pages[index]; box=page.cropbox
    x0,y0,x1,y1=map(float,(box.left,box.bottom,box.right,box.top))
    w,h=x1-x0,y1-y0;r=int(page.rotation)%360
    if r not in (0,90,180,270):raise ValueError('Поворот страницы PDF не кратен 90°.')
    vw,vh=(h,w) if r in (90,270) else (w,h)
    if position=='band':
        if r==0:y1+=BAND
        elif r==90:x0-=BAND
        elif r==180:y0-=BAND
        else:x1+=BAND
        for b in (page.mediabox,page.cropbox):
            b.lower_left=(min(float(b.left),x0),min(float(b.bottom),y0))
            b.upper_right=(max(float(b.right),x1),max(float(b.top),y1))
        vh+=BAND
    matrix={0:(1,0,0,1,x0,y0),90:(0,1,-1,0,x1,y0),180:(-1,0,0,-1,x1,y1),270:(0,-1,1,0,x0,y1)}[r]
    pdf.init_font(); tw=pdfmetrics.stringWidth(label,'DejaVu',9)+(65 if position!='band' else 0)
    glyphs=pdfmetrics.getFont('DejaVu').face.charToGlyph
    if any(ord(char) not in glyphs for char in label):raise ValueError('Шрифт не поддерживает символ подписи страницы. Измените подпись.')
    if tw>vw-100:raise ValueError('Подпись страницы слишком длинная для ширины листа.')
    total_width=tw+(65 if position!='band' else 0)
    if total_width>vw-48:raise ValueError('Номер и возврат к перечню не помещаются по ширине листа.')
    x=(vw-total_width)/2 if position=='bottom-center' else vw-24-total_width
    y=vh-19 if position in ('band','top-right') else 15
    if position!='band':
        import pypdfium2 as pdfium
        # Inspect only the intended number area on an exact one-page copy.
        single=PdfWriter();single.add_page(page);raw=BytesIO();single.write(raw)
        with pdf.PDF_LOCK,pdfium.PdfDocument(raw.getvalue()) as document:
            if not document.init_forms():
                config=pdfium.raw.FPDF_FORMFILLINFO(version=2)
                environment=pdfium.raw.FPDFDOC_InitFormFillEnvironment(document,config)
                if not environment:raise ValueError('Не удалось проверить поля PDF под номером страницы.')
                document.formenv=pdfium.PdfFormEnv(environment,config)
            source=document[0]
            bitmap=source.render(scale=1.5,crop=(max(0,x-2),max(0,y-3),max(0,vw-x-total_width-2),max(0,vh-y-11)),limit_image_cache=True,draw_annots=True)
            try:
                image=bitmap.to_pil().convert('RGB')
                if sum(min(p)<235 for p in image.get_flattened_data())>3:
                    raise ValueError(f'Номер перекрывает содержимое на странице {index+1}. Выберите безопасную верхнюю полосу.')
            finally:bitmap.close();source.close()
    out=BytesIO();c=canvas.Canvas(out,pagesize=(max(x1,1),max(y1,1)),invariant=1)
    c.transform(*matrix);c.setFont('DejaVu',9);c.setFillColorRGB(.07,.16,.29);c.drawString(x+(65 if position!='band' else 0),y,label)
    back_x=24 if position=='band' else x
    c.setFont('DejaVu',8);c.drawString(back_x,y,'Перечень')
    c.save(); overlay=PdfReader(out).pages[0]
    overlay.mediabox.lower_left=(x0,y0);overlay.mediabox.upper_right=(x1,y1)
    overlay.cropbox=deepcopy(overlay.mediabox)
    page.merge_page(overlay)
    # Inverse visual coordinates also locate the return hit rectangle.
    a,b,c_,d,e,f=matrix
    pts=[(a*xx+c_*yyy+e,b*xx+d*yyy+f) for xx,yyy in [(back_x-2,y-2),(back_x+52,y+10)]]
    xs,ys=zip(*pts);return (min(xs),min(ys),max(xs),max(ys))
