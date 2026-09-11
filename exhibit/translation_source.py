"""Extract only user-selected visible regions, excluding explicit page-edge bands."""
from io import BytesIO
import math

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject
from . import pdf


def options_for(options, count):
    if options is None:
        options = {"selection": [{"page": n+1} for n in range(count)], "top": 60, "bottom": 60, "placement": "preserve", "notes": []}
    if not isinstance(options, dict) or not {"selection", "top", "bottom"} <= set(options) or set(options)-{"selection", "top", "bottom", "placement", "notes"}:
        raise ValueError("Укажите страницы/области и верхнюю/нижнюю исключаемую зону.")
    selection = options["selection"]
    if not isinstance(selection, list) or any(not isinstance(x, dict) or set(x)-{"page", "rect"} for x in selection):
        raise ValueError("Некорректный выбор для перевода.")
    if any("rect" in x and (not isinstance(x["rect"], list) or len(x["rect"]) != 4) for x in selection):
        raise ValueError("Область должна содержать четыре координаты.")
    pdf.validate_selection(selection, count)
    for edge in ("top", "bottom"):
        value = options[edge]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 200:
            raise ValueError("Исключаемая зона должна быть от 0 до 200 pt.")
    result = {"selection": [{"page": x["page"], **({"rect": list(x["rect"])} if x.get("rect") else {})} for x in selection],
              "top": options["top"], "bottom": options["bottom"]}
    if "placement" in options or "notes" in options:
        if options.get("placement") not in ("preserve", "exclude"):
            raise ValueError("Выберите способ размещения сносок и колонтитулов.")
        notes = options.get("notes", [])
        if not isinstance(notes, list) or len(notes) > count:
            raise ValueError("Некорректные области сносок.")
        seen = set()
        for note in notes:
            if not isinstance(note, dict) or set(note) != {"page", "height"} or type(note["page"]) is not int or not 1 <= note["page"] <= count or note["page"] in seen or type(note["height"]) not in (float,int) or not math.isfinite(note["height"]) or not 0 <= note["height"] <= 400:
                raise ValueError("Укажите для каждой страницы высоту области сносок от 0 до 400 pt.")
            seen.add(note["page"])
        result.update(placement=options["placement"], notes=sorted(notes, key=lambda x:x["page"]))
    return result


def extract(data, options, limit=120_000):
    reader = PdfReader(BytesIO(data))
    options = options_for(options, len(reader.pages))
    if options.get("placement") == "preserve":
        from .translation_layout import extract as extract_layout
        return options, extract_layout(reader, options, limit)
    parts, total = [], 0
    for region, item in enumerate(options["selection"], 1):
        # Flatten rotation and cropbox offset so coordinates match the rendered page.
        writer = PdfWriter()
        page = writer.add_page(reader.pages[item["page"]-1], excluded_keys=["/Annots", "/AA"])
        if page.rotation:
            page.transfer_rotation_to_content()
        left, bottom = float(page.cropbox.left), float(page.cropbox.bottom)
        w, h = float(page.cropbox.width), float(page.cropbox.height)
        page.add_transformation(Transformation().translate(-left, -bottom))
        page.mediabox = RectangleObject((0, 0, w, h)); page.cropbox = RectangleObject((0, 0, w, h))
        rect = item.get("rect", [0, 0, 1, 1])
        x0, y0, x1, y1 = rect[0]*w, max(rect[1]*h, options["top"]), rect[2]*w, min(rect[3]*h, h-options["bottom"])
        if x0 >= x1 or y0 >= y1:
            raise ValueError(f"Страница {item['page']}, область {region}: исключаемые зоны перекрывают выбор.")
        stream = BytesIO(); writer.write(stream)
        chars, pending = [], ""
        with pdf.PDF_LOCK, pdf.pdfium.PdfDocument(stream.getvalue()) as document:
            rendered = document[0]
            textpage = rendered.get_textpage()
            try:
                if textpage.count_chars() > 250_000:
                    raise ValueError("Слишком сложный текстовый слой страницы для перевода.")
                for index in range(textpage.count_chars()):
                    code = pdf.pdfium.raw.FPDFText_GetUnicode(textpage, index)
                    char = chr(code) if 0 < code <= 0x10ffff and not 0xd800 <= code <= 0xdfff else ""
                    if char.isspace():
                        if chars: pending += char
                        continue
                    l, b, r, t = textpage.get_charbox(index)
                    # Entire glyph must fit: excluded text never leaks over a boundary.
                    if char and x0 <= l <= r <= x1 and h-y1 <= b <= t <= h-y0:
                        chars.extend([pending, char]); pending = ""
                    elif char and l < x1 and r > x0 and b < h-y0 and t > h-y1:
                        raise ValueError(f"Страница {item['page']}, область {region}: граница пересекает буквы. Увеличьте область или измените исключаемые зоны, чтобы не обрезать строку.")
                    elif chars:
                        pending = "\n"
            finally:
                textpage.close(); rendered.close()
        text = "".join(chars).replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text or "\ufffd" in text or not any(c.isalpha() for c in text):
            raise ValueError(f"Страница {item['page']}, область {region}: нет надёжно извлекаемого текста. Измените область/зоны; для сканов нужен готовый перевод PDF.")
        total += len(text)
        if total > limit:
            raise ValueError("Выбрано больше 120 000 знаков. Уменьшите выбор для перевода.")
        while text:
            split = len(text) if len(text) <= 6000 else max(text.rfind("\n", 0, 6000), text.rfind(" ", 0, 6000))
            if split <= 0: split = min(6000, len(text))
            chunk, text = text[:split], text[split:]
            parts.append({"page": item["page"], "region": region, "rect": rect, "source": chunk, "translation": ""})
    return options, parts
