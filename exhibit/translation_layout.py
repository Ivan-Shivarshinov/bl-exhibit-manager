"""Explicit page zones: keep notes and page furniture separate from body text."""
from io import BytesIO
from collections import OrderedDict
from xml.sax.saxutils import escape

from pypdf import PdfWriter, Transformation
from pypdf.generic import RectangleObject
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from . import pdf

LABELS = {"body":"Основной текст", "footnotes":"Сноски", "header":"Верхний колонтитул", "footer":"Нижний колонтитул"}


def characters(original):
    writer = PdfWriter()
    page = writer.add_page(original, excluded_keys=["/Annots", "/AA"])
    if page.rotation:
        page.transfer_rotation_to_content()
    left, bottom = float(page.cropbox.left), float(page.cropbox.bottom)
    w, h = float(page.cropbox.width), float(page.cropbox.height)
    page.add_transformation(Transformation().translate(-left, -bottom))
    page.mediabox = page.cropbox = RectangleObject((0, 0, w, h))
    stream = BytesIO(); writer.write(stream)
    result = []
    with pdf.PDF_LOCK, pdf.pdfium.PdfDocument(stream.getvalue()) as document:
        rendered = document[0]; text = rendered.get_textpage()
        try:
            if text.count_chars() > 250_000:
                raise ValueError("Слишком сложный текстовый слой страницы для перевода.")
            for i in range(text.count_chars()):
                code = pdf.pdfium.raw.FPDFText_GetUnicode(text, i)
                if not 0 < code <= 0x10ffff or 0xd800 <= code <= 0xdfff:
                    continue
                c = chr(code)
                l,b,r,t = text.get_charbox(i)
                result.append((c, (l,h-t,r,h-b)))
        finally:
            text.close(); rendered.close()
    return w,h,result


def inside_text(chars, box, page):
    x0,y0,x1,y1 = box
    admitted, pending = [], ""
    for char, (l,t,r,b) in chars:
        if char.isspace():
            if admitted: pending += char
            continue
        if x0 <= l <= r <= x1 and y0 <= t <= b <= y1:
            admitted.append((pending+char, (l,t,r,b))); pending = ""
        elif l < x1 and r > x0 and t < y1 and b > y0:
            raise ValueError(f"Страница {page}: граница зоны пересекает буквы. Передвиньте границу между строками.")
        elif admitted:
            pending = "\n"
    return admitted


def blocks(admitted):
    """Split page furniture at line breaks/large gaps, retaining a right-hand page number."""
    result, current = [], []
    for text, box in admitted:
        if current:
            previous = current[-1][1]
            if "\n" in text or abs(box[3]-previous[3]) > 5 or box[0]-previous[2] > 25:
                result.append(current); current=[]
        current.append((text.lstrip() if not current else text, box))
    if current: result.append(current)
    return result


def extract(reader, options, limit):
    parts, total, cache = [], 0, {}
    heights = {x["page"]:x["height"] for x in options.get("notes", [])}
    for region,item in enumerate(options["selection"],1):
        number = item["page"]
        if number not in cache: cache[number] = characters(reader.pages[number-1])
        w,h,chars = cache[number]
        top,bottom,notes = options["top"],options["bottom"],heights.get(number,0)
        if top+bottom+notes >= h-40:
            raise ValueError(f"Страница {number}: зоны не оставляют места основному тексту.")
        rect = item.get("rect",[0,0,1,1]); selected = [rect[0]*w,rect[1]*h,rect[2]*w,rect[3]*h]
        zones = [("header",0,top),("body",top,h-bottom-notes),("footnotes",h-bottom-notes,h-bottom),("footer",h-bottom,h)]
        region_parts = []
        for role,y0,y1 in zones:
            box = [selected[0],max(selected[1],y0),selected[2],min(selected[3],y1)]
            if box[1] >= box[3]: continue
            admitted = inside_text(chars,box,number)
            if not admitted: continue
            groups = blocks(admitted) if role in ("header","footer") else [admitted]
            for block in groups:
                source = "".join(x[0] for x in block).replace("\r\n","\n").replace("\r","\n").strip()
                if "\ufffd" in source:
                    raise ValueError(f"Страница {number}: ненадёжный текст в зоне «{LABELS[role]}».")
                bounds = [min(x[1][0] for x in block),min(x[1][1] for x in block),max(x[1][2] for x in block),max(x[1][3] for x in block)]
                total += len(source)
                if total > limit: raise ValueError("Выбрано больше 120 000 знаков. Уменьшите выбор для перевода.")
                group = len(parts)+len(region_parts)
                while source:
                    split = len(source) if len(source)<=6000 else max(source.rfind("\n",0,6000),source.rfind(" ",0,6000))
                    if split<=0:split=min(6000,len(source))
                    chunk,source = source[:split],source[split:]
                    region_parts.append({"page":number,"region":region,"rect":rect,"role":role,"group":group,
                        "layout":{"size":[w,h],"top":top,"bottom":bottom,"notes":notes,"bounds":bounds},
                        "source":chunk,"translation":""})
        if not region_parts or not any(c.isalpha() for p in region_parts for c in p["source"]):
            raise ValueError(f"Страница {number}, область {region}: нет надёжно извлекаемого текста. Для сканов нужен готовый перевод PDF.")
        parts.extend(region_parts)
    return parts


def paragraph(text, size, width):
    style = ParagraphStyle("Zone",fontName="DejaVu",fontSize=size,leading=size*1.35,splitLongWords=True)
    p = Paragraph(escape(text).replace("\t","&#160;"*4).replace("\n","<br/>"),style)
    _,height = p.wrap(width,100000)
    return p,height


def render(state):
    """One source page stays one translated page; refuse unsafe overflow without truncation."""
    stream=BytesIO(); canvas=Canvas(stream,title="Reviewed translation",invariant=1)
    pages=OrderedDict()
    for part in state["parts"]: pages.setdefault(part["page"],[]).append(part)
    for number,parts in pages.items():
        layout=parts[0]["layout"];w,h=layout["size"];canvas.setPageSize((w,h))
        groups=OrderedDict()
        for p in parts:
            key=(p["region"],p["group"])
            if key not in groups:groups[key]={**p,"translation":""}
            groups[key]["translation"]+=("\n" if groups[key]["translation"] else "")+p["translation"]
        groups=list(groups.values())
        furniture=[p for p in groups if p["role"] in ("header","footer")]
        reserved_top=max(24,layout["top"]);reserved_bottom=max(24,layout["bottom"])
        for part in furniture:
            l,t,r,b=part["layout"]["bounds"]
            same_line=[q for q in furniture if q is not part and q["role"]==part["role"] and abs(q["layout"]["bounds"][1]-t)<5]
            right=min([q["layout"]["bounds"][0]-8 for q in same_line if q["layout"]["bounds"][0]>l]+[w-24])
            left=max(24,l)
            if part["source"].strip().isdigit():
                left=max(24,r-50);right=r
            zone_end=layout["top"] if part["role"]=="header" else h-8
            later=[q["layout"]["bounds"][1]-3 for q in furniture if q["role"]==part["role"] and q["layout"]["bounds"][1]>t+5]
            available=min([zone_end]+later)-t
            fitted=None
            for size in (9,8.5,8):
                p,height=paragraph(part["translation"],size,max(1,right-left))
                if height<=available: fitted=(p,height);break
            if fitted is None:
                raise ValueError(f"Страница {number}: перевод колонтитула не помещается в свою зону. Увеличьте зону до перевода или используйте готовый PDF; текст не обрезан.")
            p,height=fitted
            if part["source"].strip().isdigit():p.style.alignment=2
            p.drawOn(canvas,left,h-t-height)
        body="\n".join(p["translation"] for p in groups if p["role"]=="body")
        notes="\n".join(p["translation"] for p in groups if p["role"]=="footnotes")
        note_bottom=max(reserved_bottom+8,min([h-p["layout"]["bounds"][3] for p in groups if p["role"]=="footnotes"] or [reserved_bottom+8]))
        width=w-96
        if width<40:raise ValueError("Страница слишком узкая для читаемого перевода.")
        fitted=None
        for body_size,note_size in ((11,9),(10.5,9),(10,8.5),(9.5,8.5),(9,8)):
            bp,bh=paragraph(body,body_size,width) if body else (None,0)
            np,nh=paragraph(notes,note_size,width) if notes else (None,0)
            available=h-reserved_top-(note_bottom+38 if notes else reserved_bottom+24)
            if bh+nh<=available:
                fitted=(bp,bh,np,nh);break
        if fitted is None:
            raise ValueError(f"Страница {number}: перевод и сноски не помещаются на одной странице при читаемом размере шрифта. Разделите выбранный материал или прикрепите свёрстанный PDF. Ничего не обрезано и не перенесено на чужую страницу.")
        bp,bh,np,nh=fitted
        if bp:bp.drawOn(canvas,48,h-reserved_top-12-bh)
        if np:
            np.drawOn(canvas,48,note_bottom)
            canvas.setStrokeColorRGB(.45,.45,.45);canvas.setLineWidth(.5)
            canvas.line(48,note_bottom+nh+6,min(w-48,190),note_bottom+nh+6)
        canvas.showPage()
    canvas.save();return stream.getvalue()
