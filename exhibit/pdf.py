"""Deterministic PDF assembly. Rectangles are normalized, with a top-left origin."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from threading import RLock
from .identifiers import stamp_label
import math

from pypdf import PdfReader, PdfWriter, PageObject, Transformation
from pypdf.generic import NameObject
import pypdfium2 as pdfium
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

PDF_LOCK = RLock()  # PDFium must never be called concurrently, even on different PDFs.
FONT = Path(__file__).parent / "assets" / "DejaVuSans.ttf"
# Long web captures are valid pages. Bound raster allocations, not paper formats.
MAX_PAGE_SIZE = 14400
MAX_PREVIEW_PIXELS = 12_000_000
MAX_RASTER_EDGE = 16000


def raster_scale(width, height, requested, max_pixels=MAX_PREVIEW_PIXELS):
    if not all(math.isfinite(v) and v > 0 for v in (width, height, requested)):
        raise ValueError("Некорректный размер страницы или масштаб PDF.")
    # Leave room for PDFium's upward rounding to whole pixels.
    return min(requested, (MAX_RASTER_EDGE-1)/width, (MAX_RASTER_EDGE-1)/height,
               math.sqrt(max_pixels/((width+1)*(height+1))))


def init_font():
    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", str(FONT)))


def inspect_pdf(data: bytes):
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("Защищённые PDF пока не поддерживаются.")
        if not 0 < len(reader.pages) <= 1000:
            raise ValueError("PDF должен содержать от 1 до 1000 страниц.")
        if reader.get_fields():
            raise ValueError("PDF с формами или подписями пока не поддерживается для обработки.")
        sizes = []
        for page in reader.pages:
            w, h = float(page.cropbox.width), float(page.cropbox.height)
            if page.rotation % 180:
                w, h = h, w
            if not (10 <= w <= MAX_PAGE_SIZE and 10 <= h <= MAX_PAGE_SIZE):
                raise ValueError(f"Размер страницы PDF должен быть от 10 до {MAX_PAGE_SIZE} пунктов по каждой стороне.")
            sizes.append([w, h])
        return {"pages": len(reader.pages), "sizes": sizes,
                "text_pages": [bool((p.extract_text() or "").strip()) for p in reader.pages]}
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Не удалось прочитать PDF. Проверьте формат и целостность файла.") from exc


def render_png(data: bytes, page_number: int, scale=1.2):
    with PDF_LOCK, pdfium.PdfDocument(data) as pdf:
        if not 1 <= page_number <= len(pdf):
            raise ValueError("Страница вне документа.")
        page = pdf[page_number - 1]
        bitmap = page.render(scale=raster_scale(*page.get_size(), scale), limit_image_cache=True)
        try:
            result = BytesIO()
            bitmap.to_pil().save(result, format="PNG")
            return result.getvalue()
        finally:
            bitmap.close()
            page.close()


def validate_selection(selection, count):
    if not selection or len(selection) > 1000:
        raise ValueError("Выберите хотя бы одну страницу или область (не более 1000).")
    for item in selection:
        if type(item.get("page")) is not int or not 1 <= item["page"] <= count:
            raise ValueError("Выбранная страница вне документа.")
        rect = item.get("rect")
        if rect is not None:
            if len(rect) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in rect):
                raise ValueError("Некорректная область страницы.")
            x0, y0, x1, y1 = rect
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and x1-x0 >= .01 and y1-y0 >= .01):
                raise ValueError("Область должна находиться внутри страницы и иметь ненулевой размер.")


def stamp(canvas_, width, height, left, right, style):
    size, margin = float(style.get("size", 10)), float(style.get("margin", 24))
    if not 6 <= size <= 24 or not 8 <= margin <= 100:
        raise ValueError("Размер штампа: 6–24 pt; отступ: 8–100 pt.")
    font = style.get("font", "DejaVu")
    if font not in ("DejaVu", "Helvetica", "Times-Roman"):
        raise ValueError("Неподдерживаемый шрифт штампа.")
    if font != "DejaVu" and any(ord(c) > 127 for c in left + right):
        raise ValueError("Для кириллицы в штампах выберите DejaVu.")
    if any("\n" in s or "\r" in s for s in (left, right)):
        raise ValueError("Штамп должен занимать одну строку.")
    if pdfmetrics.stringWidth(left, font, size) + pdfmetrics.stringWidth(right, font, size) + 18 > width - 2 * margin:
        raise ValueError("Штампы не помещаются: уменьшите размер или сократите текст пометки.")
    canvas_.setFont(font, size)
    top=float(style.get('top',26))
    if not 8 <= top <= 150:raise ValueError('Отступ сверху: 8–150 pt.')
    ascent,descent=pdfmetrics.getAscentDescent(font,size)
    if top < ascent or top-descent >= height:raise ValueError('Штамп выходит за границы страницы. Измените отступ сверху.')
    canvas_.drawString(margin, height - top, left)
    canvas_.drawRightString(width - margin, height - top, right)


def check_stamp_space(data, page_number, width, height, left, right, style):
    """Inspect only actual label rectangles, including raster images and scans."""
    if not left and not right:return
    font=style.get('font','DejaVu');size=float(style.get('size',10));margin=float(style.get('margin',24));top=float(style.get('top',26))
    ascent,descent=pdfmetrics.getAscentDescent(font,size)
    rectangles=[]
    if left:rectangles.append((margin,top-ascent,margin+pdfmetrics.stringWidth(left,font,size),top-descent))
    if right:rectangles.append((width-margin-pdfmetrics.stringWidth(right,font,size),top-ascent,width-margin,top-descent))
    with PDF_LOCK,pdfium.PdfDocument(data) as pdf:
        page=pdf[page_number-1]
        try:
            for x0,y0,x1,y1 in rectangles:
                # Render only the label rectangle at full inspection resolution.
                # A long page must not allocate a full-page 14400x14400 bitmap.
                pw,ph=page.get_size()
                x0=max(0,math.floor(x0-1));y0=max(0,math.floor(y0-1))
                x1=min(width,math.ceil(x1+1));y1=min(height,math.ceil(y1+1))
                bitmap=page.render(scale=2,crop=(x0*pw/width,(height-y1)*ph/height,(width-x1)*pw/width,y0*ph/height),limit_image_cache=True)
                try:
                    image=bitmap.to_pil().convert('RGB')
                    if sum(min(pixel)<235 for pixel in image.get_flattened_data())>2:
                        raise ValueError(f'Штамп пересекает содержимое на странице {page_number}. Измените отступы или выберите дополнительную полосу над страницей.')
                finally:bitmap.close()
        finally:page.close()


def prepare_part(data, selection, left, right, style):
    """Whole pages retain text; fragments contain ONLY pixels from the selected area."""
    init_font()
    reader = PdfReader(BytesIO(data))
    validate_selection(selection, len(reader.pages))
    writer = PdfWriter()
    for item in selection:
        source_writer = PdfWriter()
        source = source_writer.add_page(reader.pages[item["page"] - 1], excluded_keys=["/Annots", "/AA"])
        if source.rotation:
            source.transfer_rotation_to_content()
        # No copied actions, destinations or file attachments in processed output.
        source.pop(NameObject("/Annots"), None)
        source.pop(NameObject("/AA"), None)
        w, h = float(source.cropbox.width), float(source.cropbox.height)
        rect = item.get("rect")
        overlay = BytesIO()
        if rect:
            # Render then crop pixels, never crop a PDF page or paint a white mask.
            with PDF_LOCK, pdfium.PdfDocument(data) as pdf:
                page = pdf[item["page"] - 1]
                x0, y0, x1, y1 = rect
                pw,ph=page.get_size()
                bitmap = page.render(scale=raster_scale(pw*(x1-x0),ph*(y1-y0),300/72,24_000_000),
                                     crop=(x0*pw,(1-y1)*ph,(1-x1)*pw,y0*ph),limit_image_cache=True)
                try:
                    image = bitmap.to_pil().copy()
                finally:
                    bitmap.close()
                    page.close()
            fw, fh = w * (x1-x0), h * (y1-y0)
            out_w, out_h = max(w, fw + 48), fh + 68 + max(76,float(style.get('top',26))+30)
            c = canvas.Canvas(overlay, pagesize=(out_w, out_h), invariant=1)
            stamp(c, out_w, out_h, left, right, style)
            c.drawImage(ImageReader(image), 24, 68, fw, fh)
            c.setFont("DejaVu", 10)
            if item.get("omission_before"):
                c.drawString(24, fh+80, "[...]")
            if item.get("omission_after"):
                c.drawString(24, 45, "[...]")
            label = reader.page_labels[item["page"]-1]
            c.setFont("DejaVu", 8)
            c.drawString(24, 22, f"Фрагмент • страница источника {label} (лист PDF {item['page']})")
            c.showPage()
            c.save()
            writer.add_page(PdfReader(overlay).pages[0])
        else:
            overlay_mode=style.get('stamp_mode','band')=='overlay'
            # Existing projects keep their 54pt band; overlay preserves the page.
            band=0 if overlay_mode else max(54,float(style.get('top',26))+18)
            out = writer.add_blank_page(width=w, height=h+band)
            tx, ty = -float(source.cropbox.left), -float(source.cropbox.bottom)
            source.mediabox = source.cropbox
            out.merge_transformed_page(source, Transformation().translate(tx, ty))
            c = canvas.Canvas(overlay, pagesize=(w, h+band), invariant=1)
            stamp(c, w, h+band, left, right, style)
            if overlay_mode:check_stamp_space(data,item['page'],w,h,left,right,style)
            c.showPage()
            c.save()
            out.merge_page(PdfReader(overlay).pages[0])
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def assemble(original, translation, doc, style):
    if doc["mode"] == "passthrough":
        return original  # Byte-for-byte preservation, including existing stamps.
    if doc.get("designation") and doc.get("number") is None:
        raise ValueError("Задайте номер приложения или выберите обозначение «Без слова».")
    right = stamp_label(doc)
    parts = []
    if translation:
        parts.append(prepare_part(translation, doc["translation_selection"], doc["translation_label"], right, style))
    parts.append(prepare_part(original, doc["selection"], doc["original_label"], right, style))
    writer = PdfWriter()
    for part in parts:
        for page in PdfReader(BytesIO(part)).pages:
            writer.add_page(page)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()
