"""Compatibility changes to the disposable LibreOffice input, never the exported DOCX.

EMF+ decoding deliberately supports only a single, full-canvas bitmap. It is not a
metafile renderer: transforms, clipping, additional drawings and continued objects
must not be discarded to extract an apparently plausible photograph.
Record layouts: Microsoft [MS-EMFPLUS] 2.2.2.2, 2.3.4.8 and 2.3.5.1.
"""
from io import BytesIO
import posixpath
import re
import struct
from urllib.parse import unquote
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as E
from PIL import Image

from . import word

MAX_PIXELS = 25_000_000
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'


def _records(data, plus=False):
    pos, header = 0, 12 if plus else 8
    while pos < len(data):
        if pos + header > len(data):
            raise ValueError('truncated metafile record')
        if plus:
            kind, flags, size, length = struct.unpack_from('<HHII', data, pos)
        else:
            kind, size = struct.unpack_from('<II', data, pos)
            flags, length = 0, size - header
        if size < header or size % 4 or length > size - header or pos + size > len(data):
            raise ValueError('invalid metafile record size')
        yield kind, flags, data[pos+header:pos+header+length]
        pos += size


def _bitmap(data):
    if len(data) < 28:
        raise ValueError('missing bitmap')
    version, kind, width, height, stride, pixel_format, encoding = struct.unpack_from('<IIiiiII', data)
    if version >> 12 != 0xDBC01 or kind != 1:
        raise ValueError('not an EMF+ bitmap')
    if encoding != 0:
        raise ValueError('unsupported bitmap encoding')
    formats = {0x21808: ('RGB', 'BGR', 3), 0x22009: ('RGB', 'BGRX', 4),
               0x26200A: ('RGBA', 'BGRA', 4), 0xE200B: ('RGBA', 'BGRa', 4)}
    if pixel_format not in formats or width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ValueError('unsupported bitmap size or pixel format')
    mode, raw, channels = formats[pixel_format]
    # Negative-stride objects need a separate orientation implementation.
    if stride < width * channels or stride % 4 or len(data) != 28 + stride * height:
        raise ValueError('unsupported bitmap stride')
    return Image.frombytes(mode, (width, height), data[28:], 'raw', raw, stride, 1).convert('RGBA')


def emf_bitmap_png(data):
    """Return a lossless PNG for a bitmap-only EMF+, None for other EMFs.

    Unsupported EMF+ drawings fail explicitly instead of dropping drawing commands.
    Ordinary EMF and dual-mode EMF with actual GDI records remain converter-owned.
    """
    records = list(_records(data))
    if not records or records[0][0] != 1 or len(records[0][2]) < 80:
        raise ValueError('invalid EMF header')
    header = records[0][2]
    if header[32:36] != b' EMF':
        raise ValueError('invalid EMF signature')
    streams = []
    for kind, _, payload in records:
        if kind == 70 and len(payload) >= 8 and payload[4:8] == b'EMF+':
            length = struct.unpack_from('<I', payload)[0]
            if length < 4 or length > len(payload)-4:
                raise ValueError('invalid EMF+ comment')
            streams.append(payload[8:4+length])
    if not streams:
        return None
    commands = list(_records(b''.join(streams), plus=True))
    if not commands or commands[0][0] != 0x4001:
        raise ValueError('missing EMF+ header')
    if any(kind not in (1, 70, 14) for kind, _, _ in records):
        if commands[0][1] & 1:  # dual-mode with actual GDI fallback
            return None
        raise ValueError('mixed EMF drawing commands')
    bounds = struct.unpack_from('<4i', header)
    if bounds[:2] != (0, 0):
        raise ValueError('offset metafile canvas')
    objects, attributes = {}, set()
    picture, background, ended = None, (0, 0, 0, 0), False
    for index, (kind, flags, payload) in enumerate(commands):
        if ended:
            raise ValueError('commands after end of metafile')
        if kind == 0x4001 and index == 0:
            if len(payload) != 16:
                raise ValueError('invalid EMF+ header')
        elif kind == 0x4002:
            ended = True
        elif kind == 0x4030:  # page units must be pixels, at scale 1
            if flags != 2 or payload != struct.pack('<f', 1):
                raise ValueError('scaled page transform')
        elif kind == 0x4023:  # reset world transform
            if flags or payload:
                raise ValueError('invalid reset transform')
        elif kind == 0x4009:  # clear canvas, before the image only
            if picture is not None or len(payload) != 4:
                raise ValueError('unsupported clear operation')
            b, g, r, a = payload
            background = (r, g, b, a)
        elif kind == 0x4008:
            objtype, objid = flags >> 8, flags & 255
            if objid > 63 or objid in objects or objid in attributes:
                raise ValueError('reused bitmap object')
            if objtype == 5:
                objects[objid] = _bitmap(payload)
            elif objtype == 8 and len(payload) == 24:
                # Neutral image attributes: no clamp effects, full-source draw below.
                version, reserved, wrap, color, clamp, reserved2 = struct.unpack('<6I', payload)
                if version >> 12 != 0xDBC01 or reserved or reserved2 or wrap > 4 or clamp:
                    raise ValueError('unsupported image attributes')
                attributes.add(objid)
            else:
                raise ValueError('unsupported or continued image object')
        elif kind == 0x401A:
            if picture is not None or flags > 63 or len(payload) != 40:
                raise ValueError('multiple or transformed images')
            attr, units, *rects = struct.unpack('<II8f', payload)
            picture = objects.get(flags)
            if picture is None or attr not in attributes or units != 2:
                raise ValueError('missing image or attributes')
            width, height = picture.size
            if rects != [0, 0, width, height] * 2 or bounds[2:] != (width-1, height-1):
                raise ValueError('cropped or offset metafile image')
        else:
            raise ValueError('unsupported EMF+ drawing command')
    if not ended or picture is None or len(objects) != 1:
        raise ValueError('incomplete bitmap metafile')
    canvas = Image.new('RGBA', picture.size, background)
    canvas.alpha_composite(picture)
    out = BytesIO()
    canvas.save(out, format='PNG')
    return out.getvalue()


def _neutral_toc(root):
    """Override only displayed TOC text, not the shared Hyperlink style or body links."""
    fields, changed = [], False
    for node in list(root.iter()):
        if node.tag == f'{{{word.W}}}fldChar':
            kind = node.get(f'{{{word.W}}}fldCharType')
            if kind == 'begin':
                fields.append({'code': '', 'result': False})
            elif fields and kind == 'separate':
                fields[-1]['result'] = True
            elif fields and kind == 'end':
                fields.pop()
        elif node.tag == f'{{{word.W}}}instrText' and fields and not fields[-1]['result']:
            fields[-1]['code'] += node.text or ''
        elif node.tag == f'{{{word.W}}}t':
            in_toc = any(f['result'] and re.match(r'^\s*TOC\b', f['code'], re.I) for f in fields)
            in_toc |= any(a.tag == f'{{{word.W}}}fldSimple' and re.match(r'^\s*TOC\b', a.get(f'{{{word.W}}}instr', ''), re.I) for a in node.iterancestors())
            if in_toc:
                run = node.getparent()
                if run.tag != f'{{{word.W}}}r':
                    continue
                props = run.find('w:rPr', word.NS)
                if props is None:
                    props = E.Element(f'{{{word.W}}}rPr'); run.insert(0, props)
                for key, value in [('color', '000000'), ('u', 'none')]:
                    for old in list(props.findall('w:'+key, word.NS)):
                        props.remove(old)
                    E.SubElement(props, f'{{{word.W}}}{key}', {f'{{{word.W}}}val': value})
                changed = True
    return changed


def prepare_docx(data):
    parts = word.package(data)
    types = word.xml(parts['[Content_Types].xml'])
    replacements, changed = {}, False
    for name, content in list(parts.items()):
        if not name.endswith('.rels'):
            continue
        rels, dirty = word.xml(content), False
        # OPC relationship parts live beside their owner in a _rels directory.
        base = posixpath.dirname(posixpath.dirname(name))
        for rel in rels:
            if rel.get('TargetMode') == 'External' or not rel.get('Type', '').endswith('/image'):
                continue
            target = unquote(rel.get('Target', ''))
            media = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(base, target))
            if not media.lower().endswith('.emf') or media not in parts:
                continue
            if media not in replacements:
                try:
                    png = emf_bitmap_png(parts[media])
                except (ValueError, OSError, struct.error) as exc:
                    raise ValueError(f'LibreOffice: не удалось безопасно подготовить изображение {posixpath.basename(media)}. Вставьте его в исходный Word как PNG/JPEG и загрузите DOCX заново. Исходник сохранён.') from exc
                if png is None:
                    replacements[media] = None
                else:
                    new = media + '.png'
                    while new in parts:
                        new += '.png'
                    parts[new] = png
                    E.SubElement(types, f'{{{CT}}}Override', PartName='/'+new, ContentType='image/png')
                    replacements[media] = new
            if replacements[media]:
                rel.set('Target', posixpath.relpath(replacements[media], base or '.'))
                dirty = True
        if dirty:
            parts[name] = E.tostring(rels, xml_declaration=True, encoding='UTF-8', standalone=True)
            changed = True
    for name, content in list(parts.items()):
        if name.startswith('word/') and name.endswith('.xml'):
            root = word.xml(content)
            if _neutral_toc(root):
                parts[name] = E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
                changed = True
    if not changed:
        return data
    parts['[Content_Types].xml'] = E.tostring(types, xml_declaration=True, encoding='UTF-8', standalone=True)
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as z:
        for name, content in parts.items():
            z.writestr(name, content)
    return out.getvalue()
