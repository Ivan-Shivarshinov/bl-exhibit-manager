"""Tag only Writer's footnote rules in disposable FODT, then restore DOCX offsets.

Writer discards special-footnote paragraph indents at DOCX import. A unique
temporary rule colour identifies its PDF path without guessing from page text.
The tagged PDF structure identifies continued notes; other drawings stay intact.
"""
from io import BytesIO
import re
from lxml import etree as E
from pypdf import PdfReader, PdfWriter
from pypdf.generic import FloatObject, NameObject
from . import word

STYLE='urn:oasis:names:tc:opendocument:xmlns:style:1.0'
FO='urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0'
ERROR='LibreOffice: не удалось безопасно сохранить разделитель сносок. Исходный DOCX сохранён. Проверьте оформление разделителя в Word или используйте Microsoft Word на Windows для основного PDF.'

def separator_offsets(data):
    parts=word.package(data)
    if word.FOOT not in parts:return None
    body=word.xml(parts['word/document.xml'])
    if not body.findall('.//w:footnoteReference',word.NS):return None
    styles=word.xml(parts['word/styles.xml']) if 'word/styles.xml' in parts else E.Element('styles')
    byid={s.get(f'{{{word.W}}}styleId'):s for s in styles.findall('w:style',word.NS)}
    defaults=styles.find('w:docDefaults/w:pPrDefault/w:pPr',word.NS)
    defaultstyle=next((s.get(f'{{{word.W}}}styleId') for s in byid.values() if s.get(f'{{{word.W}}}type')=='paragraph' and s.get(f'{{{word.W}}}default')=='1'),None)
    root=word.xml(parts[word.FOOT]); result={}
    for kind in ('separator','continuationSeparator'):
        note=next((n for n in root if n.get(f'{{{word.W}}}type')==kind),None)
        if note is None:result[kind]=0.;continue
        ps=note.findall('w:p',word.NS)
        if len(ps)!=1 or note.findall('.//w:t',word.NS) or note.findall('.//w:drawing',word.NS):raise ValueError(ERROR)
        props=ps[0].find('w:pPr',word.NS)
        style=props.find('w:pStyle',word.NS) if props is not None else None
        sid=style.get(f'{{{word.W}}}val') if style is not None else defaultstyle
        chain=[];seen=set()
        while sid in byid and sid not in seen:
            seen.add(sid);s=byid[sid];chain.insert(0,s.find('w:pPr',word.NS));parent=s.find('w:basedOn',word.NS);sid=parent.get(f'{{{word.W}}}val') if parent is not None else None
        ind={};jc='left'
        for p in [defaults,*chain,props]:
            if p is None:continue
            align=p.find('w:jc',word.NS)
            if align is not None:jc=align.get(f'{{{word.W}}}val','left')
            node=p.find('w:ind',word.NS)
            if node is not None:
                attrs={E.QName(k).localname:v for k,v in node.attrib.items()}
                if 'firstLine' in attrs:ind.pop('hanging',None)
                if 'hanging' in attrs:ind.pop('firstLine',None)
                ind.update(attrs)
        if jc not in ('left','start','both') or any(k.endswith('Chars') for k in ind):raise ValueError(ERROR)
        try:result[kind]=(int(ind.get('start',ind.get('left',0)))+int(ind.get('firstLine',0))-int(ind.get('hanging',0)))/20
        except (TypeError,ValueError):raise ValueError(ERROR) from None
    return result

def _points(value):
    match=re.fullmatch(r'(-?\d+(?:\.\d+)?)(cm|mm|in|pt)',value or '')
    if not match:raise ValueError(ERROR)
    return float(match[1])*{'cm':72/2.54,'mm':72/25.4,'in':72,'pt':1}[match[2]]

def tag_rules(data):
    root=word.xml(data); used=set(re.findall(r'#[0-9a-fA-F]{6}',data.decode('utf-8')));tags={}
    for layout in root.findall('.//{'+STYLE+'}page-layout'):
        props=layout.find('{'+STYLE+'}page-layout-properties')
        if props is None:continue
        rule=props.find('{'+STYLE+'}footnote-sep')
        if rule is None or rule.get('{'+STYLE+'}rel-width')=='0%':continue
        width=_points(props.get('{'+FO+'}page-width'))-_points(props.get('{'+FO+'}margin-left'))-_points(props.get('{'+FO+'}margin-right'))
        if rule.get('{'+STYLE+'}adjustment')!='left':raise ValueError(ERROR)
        color=next('#'+f'{n:06x}' for n in range(0x092137,0x092237) if '#'+f'{n:06x}' not in {c.lower() for c in used})
        used.add(color)
        original=rule.get('{'+STYLE+'}color','#000000')
        tags[color]={'width':width,'original':original}
        rule.set('{'+STYLE+'}color',color)
    return E.tostring(root,xml_declaration=True,encoding='UTF-8'),tags

def _note_marks(reader):
    pageids={p.indirect_reference.idnum:i for i,p in enumerate(reader.pages)};marks={};seen=set()
    def visit(value,page=None,note=None):
        if hasattr(value,'idnum'):
            if value.idnum in seen:return
            seen.add(value.idnum)
        value=value.get_object() if hasattr(value,'get_object') else value
        if isinstance(value,list):
            for v in value:visit(v,page,note)
        elif isinstance(value,dict):
            pg=value.get('/Pg')
            if pg is not None:page=pageids.get(pg.idnum)
            if value.get('/S')=='/Note':note=page
            if '/MCID' in value and note is not None and page is not None:marks[(page,int(value['/MCID']))]=note
            if '/K' in value:visit(value['/K'],page,note)
        elif isinstance(value,int) and note is not None and page is not None:marks[(page,int(value))]=note
    struct=reader.trailer['/Root'].get('/StructTreeRoot')
    if struct:visit(struct.get_object().get('/K',[]))
    return marks

def restore_rules(data,tags,offsets):
    reader=PdfReader(BytesIO(data));marks=_note_marks(reader);writer=PdfWriter(clone_from=reader)
    rgb=lambda color:tuple(int(color[i:i+2],16)/255 for i in (1,3,5))
    tagcolors={rgb(k):v for k,v in tags.items()}
    def tag(vals):
        return next((v for c,v in tagcolors.items() if len(vals)==3 and all(abs(float(a)-b)<1e-5 for a,b in zip(vals,c))),None)
    changed=0
    for pi,page in enumerate(writer.pages):
        stream=page.get_contents()
        if stream is None:continue
        ops=stream.operations
        firstnote=next((marks[(pi,int(v[1]['/MCID']))] for v,op in ops if op==b'BDC' and len(v)==2 and isinstance(v[1],dict) and '/MCID' in v[1] and (pi,int(v[1]['/MCID'])) in marks),None)
        current=None;paths=[];targets=[];colors=[]
        for i,(vals,op) in enumerate(ops):
            if op==b'EMC':
                if paths:raise ValueError(ERROR)
                current=None
            elif op in (b'rg',b'RG'):
                found=tag(vals)
                if found:current=found;colors.append(i)
                elif op==b'rg':current=None
            elif current and op in (b'm',b'l'):
                paths.append(i)
            elif current and op in (b'f',b'f*',b'S',b'B',b'B*'):
                if not paths or firstnote is None:raise ValueError(ERROR)
                xs=[float(ops[i][0][0]) for i in paths];ys=[float(ops[i][0][1]) for i in paths]
                if len(paths)!=5 or max(ys)-min(ys)>2 or abs(max(xs)-min(xs)-144)>1:raise ValueError(ERROR)
                targets.append((list(paths),min(xs),max(xs),current));paths=[]
            elif current and op not in (b'q',b'Q',b'h',b'w',b'J',b'j',b'd',b'rg',b'RG',b'EMC'):
                raise ValueError(ERROR)
        if not targets:continue
        if len(targets) not in (1,2):raise ValueError(ERROR)
        continued=firstnote<pi;dx=offsets['continuationSeparator' if continued else 'separator']
        for indices,xmin,xmax,t in targets:
            newwidth=t['width']-dx if continued else xmax-xmin
            for idx in indices:
                vals=ops[idx][0];vals[0]=FloatObject(xmin+dx+(float(vals[0])-xmin)*newwidth/(xmax-xmin))
        for idx in colors:
            vals,op=ops[idx];ops[idx]=([FloatObject(v) for v in rgb(targets[0][3]['original'])],op)
        page.replace_contents(stream);changed+=1
    if not changed:raise ValueError(ERROR)
    out=BytesIO();writer.write(out);return out.getvalue()
