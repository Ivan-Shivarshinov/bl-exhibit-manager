"""Local font family inventory; does not read or transmit document text."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import ctypes, os, re, struct, subprocess, sys, time
from . import word
from pypdf import PdfReader

def normalized(name):return re.sub(r'[^\w]','',name.casefold())

def _font_names(path):
    names=set()
    try:
        with path.open('rb') as f:
            header=f.read(12)
            if len(header)<12:return names
            offsets=[0]
            if header[:4]==b'ttcf':
                count=struct.unpack('>I',header[8:12])[0]
                if count>128:return names
                offsets=list(struct.unpack('>'+str(count)+'I',f.read(count*4)))
            for offset in offsets:
                f.seek(offset);head=f.read(12)
                if len(head)!=12 or head[:4] not in (b'OTTO',b'\0\1\0\0',b'true'):continue
                count=struct.unpack('>H',head[4:6])[0]
                if count>256:continue
                tables=f.read(count*16)
                for pos in range(0,len(tables)-15,16):
                    tag,_,where,size=struct.unpack('>4sIII',tables[pos:pos+16])
                    if tag!=b'name' or size>1_000_000:continue
                    f.seek(where);table=f.read(size)
                    if len(table)<6:continue
                    _,count,start=struct.unpack('>3H',table[:6])
                    for i in range(min(count,(len(table)-6)//12)):
                        platform,_,_,kind,length,relative=struct.unpack('>6H',table[6+i*12:18+i*12])
                        if kind not in (1,16):continue
                        value=table[start+relative:start+relative+length]
                        names.add(value.decode('utf-16-be' if platform in (0,3) else 'mac_roman').strip())
    except (OSError,ValueError,UnicodeError,struct.error):pass
    return names

def _mac_families():
    core=ctypes.CDLL('/System/Library/Frameworks/CoreText.framework/CoreText')
    cf=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    core.CTFontManagerCopyAvailableFontFamilyNames.restype=ctypes.c_void_p
    cf.CFArrayGetCount.argtypes=[ctypes.c_void_p];cf.CFArrayGetCount.restype=ctypes.c_long
    cf.CFArrayGetValueAtIndex.argtypes=[ctypes.c_void_p,ctypes.c_long];cf.CFArrayGetValueAtIndex.restype=ctypes.c_void_p
    cf.CFStringGetCString.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_long,ctypes.c_uint32];cf.CFStringGetCString.restype=ctypes.c_bool
    cf.CFRelease.argtypes=[ctypes.c_void_p]
    array=core.CTFontManagerCopyAvailableFontFamilyNames();names=set()
    if not array:return names
    try:
        for i in range(cf.CFArrayGetCount(array)):
            buffer=ctypes.create_string_buffer(4096)
            if cf.CFStringGetCString(cf.CFArrayGetValueAtIndex(array,i),buffer,len(buffer),0x08000100):names.add(buffer.value.decode('utf-8'))
    finally:cf.CFRelease(array)
    return names

@lru_cache(maxsize=8)
def _inventory(executable,bucket):
    names=set();checked=False
    if sys.platform=='darwin':
        try:names.update(_mac_families());checked=bool(names)
        except (OSError,AttributeError):pass
    elif sys.platform=='win32':
        dirs=[Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts',Path(os.environ.get('LOCALAPPDATA',''))/'Microsoft/Windows/Fonts']
        for directory in dirs:
            for path in directory.glob('*'):
                if path.suffix.lower() in ('.ttf','.ttc','.otf'):names.update(_font_names(path))
        checked=bool(names)
    else:
        try:
            result=subprocess.run(['fc-list','--format=%{family}\n'],capture_output=True,text=True,timeout=5,check=True)
            names.update(n.strip() for line in result.stdout.splitlines() for n in line.split(','));checked=bool(names)
        except (OSError,subprocess.SubprocessError):pass
    if executable:
        base=Path(executable).parent.parent
        if sys.platform=='darwin':base=Path(executable).parent.parent/'Resources'
        for directory in [base/'share/fonts',base/'Fonts',base/'fonts']:
            for path in directory.rglob('*'):
                if path.suffix.lower() in ('.ttf','.ttc','.otf'):names.update(_font_names(path))
    return {normalized(n) for n in names if n},checked

def required_families(data):
    parts=word.package(data);styles=word.xml(parts['word/styles.xml']) if 'word/styles.xml' in parts else None
    byid={s.get(f'{{{word.W}}}styleId'):s for s in styles.findall('w:style',word.NS)} if styles is not None else {}
    defaults=styles.find('w:docDefaults/w:rPrDefault/w:rPr',word.NS) if styles is not None else None
    normal=next((s.get(f'{{{word.W}}}styleId') for s in byid.values() if s.get(f'{{{word.W}}}type')=='paragraph' and s.get(f'{{{word.W}}}default')=='1'),None)
    theme={}
    if 'word/theme/theme1.xml' in parts:
        root=word.xml(parts['word/theme/theme1.xml']);A='http://schemas.openxmlformats.org/drawingml/2006/main'
        for kind in ['major','minor']:
            n=root.find('.//{'+A+'}'+kind+'Font/{'+A+'}latin')
            if n is not None:theme[kind]=n.get('typeface')
    def props(sid):
        result=[];seen=set()
        while sid in byid and sid not in seen:
            seen.add(sid);s=byid[sid];result.insert(0,s.find('w:rPr',word.NS));b=s.find('w:basedOn',word.NS);sid=b.get(f'{{{word.W}}}val') if b is not None else None
        return result
    required=set()
    for name,part in parts.items():
        if not re.fullmatch(r'word/(document|footnotes|endnotes|header\d+|footer\d+)\.xml',name):continue
        root=word.xml(part)
        for p in root.findall('.//w:p',word.NS):
            pstyle=p.find('w:pPr/w:pStyle',word.NS);sid=pstyle.get(f'{{{word.W}}}val') if pstyle is not None else normal
            base=[defaults,*props(sid)]
            for r in p.findall('.//w:r',word.NS):
                if not r.findall('w:t',word.NS):continue
                charstyle=r.find('w:rPr/w:rStyle',word.NS);rid=charstyle.get(f'{{{word.W}}}val') if charstyle is not None else None
                family=None
                for pr in [*base,*props(rid),r.find('w:rPr',word.NS)]:
                    font=pr.find('w:rFonts',word.NS) if pr is not None else None
                    if font is None:continue
                    candidate=font.get(f'{{{word.W}}}ascii') or font.get(f'{{{word.W}}}hAnsi')
                    key=font.get(f'{{{word.W}}}asciiTheme') or font.get(f'{{{word.W}}}hAnsiTheme')
                    if key:candidate=theme.get('major' if key.startswith('major') else 'minor') or candidate
                    if candidate:family=candidate
                if family:required.add(family)
    return sorted(required,key=str.casefold)

def inspect(data,engine):
    required=required_families(data)
    available,checked=_inventory(engine.get('executable',''),int(time.monotonic()//30))
    missing=[name for name in required if normalized(name) not in available] if checked else []
    return {'checked':checked,'required':required,'missing':missing,'message':
            'Не найдены шрифты: '+', '.join(missing)+'. Установите их в систему для всех приложений, перезапустите приложение и конвертер и подготовьте PDF заново. При замене шрифта переносы страниц и положение подписи могут отличаться от Word.' if missing else
            'Доступность шрифтов не удалось проверить. Сверьте шрифты и переносы в основном PDF.' if not checked else ''}

def pdf_report(data,required):
    reader=PdfReader(BytesIO(data));names=set();seen=set()
    def resources(root):
        root=root.get_object() if hasattr(root,'get_object') else root
        fonts=root.get('/Font',{})
        fonts=fonts.get_object() if hasattr(fonts,'get_object') else fonts
        for f in fonts.values():
            f=f.get_object();name=str(f.get('/BaseFont','')).lstrip('/')
            if name:names.add(re.sub(r'^[A-Z]{6}\+','',name))
        objects=root.get('/XObject',{})
        objects=objects.get_object() if hasattr(objects,'get_object') else objects
        for v in objects.values():
            key=(getattr(v,'idnum',None),getattr(v,'generation',None))
            if key in seen:continue
            seen.add(key);o=v.get_object()
            if o.get('/Subtype')=='/Form' and '/Resources' in o:resources(o['/Resources'])
    for page in reader.pages:resources(page.get('/Resources',{}))
    missing=[family for family in required if not any(normalized(name).startswith(normalized(family)) for name in names)]
    return {'names':sorted(names),'unconfirmed':missing,'message':
            'В созданном PDF не подтверждены шрифты: '+', '.join(missing)+'. Возможна замена шрифтов конвертером. Сверьте переносы и подпись с Word; установите требуемые шрифты для конвертера и подготовьте PDF заново.' if missing else ''}
